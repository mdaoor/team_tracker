"""Built-in policy templates: YAML -> parameter form -> CEL.

CEL source syntax inside a template:
  `{name}`            the parameter as a typed CEL literal (numbers bare, strings quoted, `ident` raw)
  `{name|expr}`       the parameter, or the CEL expression `expr` when it is unset
  `<<?name ... ?>>`   the text only when the parameter is set (it may use `{name}`)
`detail` (the violation's human text) uses `{cel expression}` placeholders evaluated against the same context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from tt_cel.errors import CelError

DIR = Path(__file__).parent / "templates" / "builtin"
TYPES = {"int", "number", "string", "ident", "bool"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    default: Any = None
    min: float | None = None
    max: float | None = None
    help: str = ""


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    category: str
    severity: str
    target: str
    trigger: str
    cel: str
    detail: str
    params: tuple[Param, ...] = ()
    schedule: str | None = None
    event_types: tuple[str, ...] = ()
    candidate_filter: dict[str, Any] | None = None
    grouping: str | None = None
    example: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "description": self.description, "category": self.category,
            "severity": self.severity, "target": self.target, "trigger": self.trigger, "schedule": self.schedule,
            "event_types": list(self.event_types), "cel_source": self.cel, "detail": self.detail,
            "candidate_filter": self.candidate_filter, "grouping": self.grouping, "example": self.example,
            "params": [p.__dict__ for p in self.params],
        }  # fmt: skip


def _template(d: dict[str, Any]) -> Template:
    known = {
        "key",
        "name",
        "description",
        "category",
        "severity",
        "target",
        "trigger",
        "cel",
        "detail",
        "params",
        "schedule",
        "event_types",
        "candidate_filter",
        "grouping",
        "example",
    }
    return Template(
        **{k: v for k, v in d.items() if k in known and k not in {"params", "event_types"}},
        params=tuple(Param(**p) for p in d.get("params", [])),
        event_types=tuple(d.get("event_types", ())),
        extra={k: v for k, v in d.items() if k not in known},
    )


@cache
def builtin_templates() -> dict[str, Template]:
    out = {}
    for f in sorted(DIR.glob("*.yaml")):
        t = _template(yaml.safe_load(f.read_text()))
        out[t.key] = t
    return out


def check_params(params: tuple[Param, ...] | list[Param], values: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults and validate type and bounds; unknown parameters are errors (a typo must not silently change a rule)."""
    by = {p.name: p for p in params}
    for k in values:
        if k not in by:
            raise CelError(
                f"unknown parameter {k!r}; this template has: {', '.join(by) or 'none'}", field=f"params.{k}"
            )
    out: dict[str, Any] = {}
    for p in params:
        v = values.get(p.name, p.default)
        if v is None:
            out[p.name] = None
            continue
        ok = {
            "int": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, int | float) and not isinstance(v, bool),
            "string": isinstance(v, str),
            "ident": isinstance(v, str) and bool(_IDENT.match(v)),
            "bool": isinstance(v, bool),
        }[p.type]
        if not ok:
            raise CelError(f"parameter {p.name!r} must be a {p.type}", field=f"params.{p.name}")
        if p.type in {"int", "number"} and (
            (p.min is not None and v < p.min) or (p.max is not None and v > p.max)
        ):
            raise CelError(
                f"parameter {p.name!r} must be between {p.min} and {p.max}", field=f"params.{p.name}"
            )
        out[p.name] = v
    return out


def literal(v: Any, type_: str) -> str:
    if type_ == "ident":
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


_COND = re.compile(r"<<\?(\w+)(.*?)\?>>", re.S)
_ALT = re.compile(r"\{(\w+)\|([^{}]*)\}")
_REF = re.compile(r"\{(\w+)\}")


def render(
    source: str, params: tuple[Param, ...] | list[Param], values: dict[str, Any], *, text: bool = False
) -> str:
    """Expand a template source with checked values. Raises `CelError` for a parameter the source needs but is unset.

    `text=True` is for a `detail` string: parameters appear as plain text and an unset `{name|expr}` stays a `{expr}` placeholder.
    """
    vals = check_params(params, values)
    types = {p.name: p.type for p in params}

    def cond(m: re.Match[str]) -> str:
        return _REF.sub(ref, m.group(2)) if vals.get(m.group(1)) is not None else ""

    def alt(m: re.Match[str]) -> str:
        n = m.group(1)
        if vals.get(n) is not None:
            return str(vals[n]) if text else literal(vals[n], types[n])
        return "{" + m.group(2).strip() + "}" if text else m.group(2).strip()

    def ref(m: re.Match[str]) -> str:
        n = m.group(1)
        if n not in vals:
            raise CelError(f"template refers to unknown parameter {n!r}", field=f"params.{n}")
        if vals[n] is None:
            raise CelError(f"parameter {n!r} is required", field=f"params.{n}")
        return str(vals[n]) if text else literal(vals[n], types[n])

    return _REF.sub(ref, _ALT.sub(alt, _COND.sub(cond, source))).strip()


def placeholders(detail: str) -> list[str]:
    """CEL expressions inside a `detail` format string."""
    out, depth, cur = [], 0, ""
    for ch in detail:
        if ch == "{":
            depth += 1
            if depth == 1:
                cur = ""
                continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                out.append(cur)
                continue
        if depth >= 1:
            cur += ch
    return out
