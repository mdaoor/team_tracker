"""Compile and evaluate policy expressions (celpy), with length / size limits and typed Python <-> CEL conversion."""

from __future__ import annotations

import re
import re._parser as re_parser  # type: ignore[import-not-found]
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import celpy
from celpy import celtypes
from celpy.celparser import CELParseError
from celpy.evaluation import CELEvalError
from lark import Token, Tree

from tt_cel.checker import check_tree
from tt_cel.errors import CelError
from tt_cel.schema import FUNCTIONS, SCHEMA_V1, Schema
from tt_cel.templates import Param, placeholders, render

MAX_SOURCE = 4096
MAX_NODES = 400
MAX_DEPTH = 40
_ENV = celpy.Environment()
_WINDOW = re.compile(r'^"(today|\d+[dhw])"$')


def to_cel(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return celtypes.BoolType(v)
    if isinstance(v, int):
        return celtypes.IntType(v)
    if isinstance(v, float):
        return celtypes.DoubleType(v)
    if isinstance(v, str):
        return celtypes.StringType(v)
    if isinstance(v, datetime):
        return celtypes.TimestampType(v if v.tzinfo else v.replace(tzinfo=UTC))
    if isinstance(v, date):
        return celtypes.TimestampType(datetime(v.year, v.month, v.day, tzinfo=UTC))
    if isinstance(v, timedelta):
        return celtypes.DurationType(v)
    if isinstance(v, dict):
        return celtypes.MapType({celtypes.StringType(k): to_cel(x) for k, x in v.items()})
    if isinstance(v, list | tuple | set):
        return celtypes.ListType([to_cel(x) for x in v])
    return celtypes.StringType(str(v))


def from_cel(v: Any) -> Any:
    if isinstance(v, celtypes.BoolType):
        return bool(v)
    if isinstance(v, celtypes.TimestampType):
        return v
    return v


def fmt(v: Any) -> str:
    """Human text for a value inside a violation's `detail`."""
    if v is None:
        return "—"
    if isinstance(v, celtypes.BoolType | bool):
        return "true" if v else "false"
    if isinstance(v, celtypes.IntType | int):
        return str(int(v))
    if isinstance(v, celtypes.DoubleType | float):
        return f"{float(v):g}"
    if isinstance(v, celtypes.TimestampType):
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return (
            dt.date().isoformat()
            if (dt.hour, dt.minute, dt.second) == (0, 0, 0)
            else dt.isoformat(timespec="minutes")
        )
    if isinstance(v, celtypes.ListType | list):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    return str(v)


def _scan(tree: Tree[Any]) -> tuple[set[str], set[str]]:
    """(fact groups needed by the functions called, time windows named in string literals)."""
    by_name = {f.name: f for f in FUNCTIONS}
    groups: set[str] = set()
    windows: set[str] = set()
    for t in tree.iter_subtrees():
        if t.data in {"member_dot_arg", "ident_arg"}:
            name = str(t.children[1] if t.data == "member_dot_arg" else t.children[0])
            f = by_name.get(name)
            if f and f.needs:
                groups.add(f.needs)
        for c in t.children:
            if isinstance(c, Token) and c.type == "STRING_LIT" and _WINDOW.match(str(c)):
                windows.add(str(c).strip('"'))
    return groups, windows


@dataclass
class Compiled:
    source: str
    tree: Tree[Any]
    fact_groups: set[str] = field(default_factory=set)
    windows: set[str] = field(default_factory=set)

    def evaluate(
        self, context: dict[str, Any], functions: dict[str, Callable[..., Any]], *, now: datetime
    ) -> Any:
        prog = _ENV.program(
            self.tree, functions={**functions, "matches": safe_matches}
        )  # cheap: the tree is shared, only the closure set differs
        act = {k: to_cel(v) for k, v in context.items()}
        act["now"] = to_cel(now)
        try:
            return from_cel(prog.evaluate(act))
        except CELEvalError as exc:
            raise CelError(f"evaluation failed: {exc}") from exc
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise CelError(f"evaluation failed: {exc}") from exc


MAX_PATTERN = 200
MAX_SUBJECT = 10_000
_PATTERNS: dict[str, re.Pattern[str]] = {}


def _nested_repeat(parsed: Any, inside: bool = False) -> bool:
    """True for a quantifier inside a quantifier, the shape behind catastrophic backtracking (`(a+)+`, `(.*)*`)."""
    for op, av in parsed:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            if inside and av[1] > 1:
                return True
            if _nested_repeat(av[2], True):
                return True
        elif name == "SUBPATTERN" and _nested_repeat(av[-1], inside):
            return True
        elif name == "BRANCH" and any(_nested_repeat(b, inside) for b in av[1]):
            return True
    return False


def safe_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a user regex under limits: bounded length, no nested quantifiers, no back-references."""
    cached = _PATTERNS.get(pattern)
    if cached is not None:
        return cached
    if len(pattern) > MAX_PATTERN:
        raise CelError(f"regular expression too long (limit {MAX_PATTERN} characters)")
    try:
        parsed = re_parser.parse(pattern)
    except re.error as exc:
        raise CelError(f"invalid regular expression: {exc}") from exc
    if _nested_repeat(parsed) or any(str(op) == "GROUPREF" for op, _ in parsed):
        raise CelError(
            "this regular expression can backtrack without bound; simplify it (no nested quantifiers)"
        )
    compiled = re.compile(pattern)
    if len(_PATTERNS) < 512:
        _PATTERNS[pattern] = compiled
    return compiled


def safe_matches(subject: Any, pattern: Any) -> Any:
    """CEL `matches` with bounded patterns and subjects (celpy would hand the raw user pattern to `re`)."""
    text = str(subject)
    if len(text) > MAX_SUBJECT:
        text = text[:MAX_SUBJECT]
    return celtypes.BoolType(safe_pattern(str(pattern)).search(text) is not None)


def _limits(tree: Tree[Any]) -> None:
    n = sum(1 for _ in tree.iter_subtrees())
    if n > MAX_NODES:
        raise CelError(f"expression too complex ({n} nodes; limit {MAX_NODES})")

    def depth(t: Any) -> int:
        return 1 + max((depth(c) for c in t.children if isinstance(c, Tree)), default=0)

    if depth(tree) > MAX_DEPTH * 3:  # the grammar wraps every operator level; this allows ~40 real nestings
        raise CelError("expression nested too deeply")


def compile(source: str, schema: Schema = SCHEMA_V1, *, expect_bool: bool = True) -> Compiled:  # noqa: A001
    """Parse, bound and type-check `source` against the context schema. Raises `CelError` naming field and position."""
    if not source or not source.strip():
        raise CelError("the condition is empty")
    if len(source) > MAX_SOURCE:
        raise CelError(f"the condition is {len(source)} characters; the limit is {MAX_SOURCE}")
    try:
        tree = _ENV.compile(source)
    except CELParseError as exc:
        line = getattr(exc, "line", None)
        col = getattr(exc, "column", None)
        raise CelError(f"syntax error: {exc.args[0] if exc.args else exc}", line=line, column=col) from exc
    _limits(tree)
    check_tree(tree, schema, expect_bool=expect_bool)
    groups, windows = _scan(tree)
    return Compiled(source, tree, groups, windows)


@dataclass
class DetailTemplate:
    parts: list[str | Compiled]

    @property
    def fact_groups(self) -> set[str]:
        return set().union(*(p.fact_groups for p in self.parts if isinstance(p, Compiled)))

    @property
    def windows(self) -> set[str]:
        return set().union(*(p.windows for p in self.parts if isinstance(p, Compiled)))

    def render(
        self, context: dict[str, Any], functions: dict[str, Callable[..., Any]], *, now: datetime
    ) -> str:
        return "".join(
            p if isinstance(p, str) else fmt(p.evaluate(context, functions, now=now)) for p in self.parts
        )


def compile_detail(
    detail: str, params: tuple[Param, ...] | list[Param], values: dict[str, Any], schema: Schema = SCHEMA_V1
) -> DetailTemplate:
    """`"{item.stage.key} for {item.days_in_stage()}d"` -> parts; `{param}` placeholders are filled, the rest are CEL expressions."""
    text = render(detail, params, values, text=True)
    exprs = placeholders(text)
    parts: list[str | Compiled] = []
    rest = text
    for e in exprs:
        head, _, rest = rest.partition("{" + e + "}")
        if head:
            parts.append(head)
        parts.append(compile(e, schema, expect_bool=False))
    if rest:
        parts.append(rest)
    return DetailTemplate(parts)
