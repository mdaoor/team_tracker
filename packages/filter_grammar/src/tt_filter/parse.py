"""JSON filter -> AST. Pure validation: shapes, enums, depth, relative tokens. Lookups (dimension keys, handles, team slugs)
are validated by the compiler against the workspace, which raises the same `FilterError` with the same pointers."""

from __future__ import annotations

from typing import Any

from tt_filter.ast import And, Clause, FilterError, Node, Not, Or
from tt_filter.dates import is_token

MAX_DEPTH = 4
PRIORITIES = ("lowest", "low", "medium", "high", "highest", "critical")
STATE_CATEGORIES = ("open", "active", "done")
DATE_KEYS = ("due_date", "planned_end", "planned_start", "actual_end", "created")
SEVERITIES = ("info", "warning", "violation", "critical")
KEYS = (
    "dims", "people", "people2", "priority", "weight", "impact", *DATE_KEYS, "touched", "state", "stage", "type",
    "tags", "risk", "violations", "text", "project", "include_inbox", "and", "or", "not",
)  # fmt: skip


def _obj(v: Any, ptr: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise FilterError(ptr, "expected an object")
    return v


def _only(v: dict[str, Any], allowed: tuple[str, ...], ptr: str) -> None:
    for k in v:
        if k not in allowed:
            raise FilterError(f"{ptr}/{k}", f"unknown key; allowed: {', '.join(allowed)}")


def _strs(v: Any, ptr: str) -> list[str]:
    items = v if isinstance(v, list) else [v]
    if not items or not all(isinstance(x, str) and x for x in items):
        raise FilterError(ptr, "expected a non-empty string or list of strings")
    return list(items)


def _num(v: Any, ptr: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise FilterError(ptr, "expected a number")
    return float(v)


def parse(raw: Any, *, _depth: int = 1, _ptr: str = "") -> Node:
    """Parse one filter object. Top-level keys are AND-ed; `and`/`or`/`not` compose sub-filters (depth <= 4)."""
    doc = _obj(raw, _ptr)
    if _depth > MAX_DEPTH:
        raise FilterError(_ptr, f"filters nest at most {MAX_DEPTH} levels")
    parts: list[Node] = []
    for key, val in doc.items():
        ptr = f"{_ptr}/{key}"
        if key == "and" or key == "or":
            if not isinstance(val, list) or not val:
                raise FilterError(ptr, "expected a non-empty array of filters")
            subs = tuple(parse(x, _depth=_depth + 1, _ptr=f"{ptr}/{i}") for i, x in enumerate(val))
            parts.append(And(subs) if key == "and" else Or(subs))
        elif key == "not":
            parts.append(Not(parse(val, _depth=_depth + 1, _ptr=ptr)))
        elif key in KEYS:
            parts.append(_clause(key, val, ptr))
        else:
            raise FilterError(ptr, f"unknown filter key {key!r}; allowed: {', '.join(KEYS)}")
    if not parts:
        return And(())
    return parts[0] if len(parts) == 1 else And(tuple(parts))


def _clause(key: str, val: Any, ptr: str) -> Clause:
    fn = _PARSERS[key]
    return Clause(key, fn(val, ptr), ptr)


def _dims(v: Any, ptr: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for dim, spec in _obj(v, ptr).items():
        p = f"{ptr}/{dim}"
        if isinstance(spec, str):
            out[dim] = {"in": [spec]}
        elif isinstance(spec, list):
            out[dim] = {"in": _strs(spec, p)}
        else:
            s = _obj(spec, p)
            _only(s, ("in", "not_in", "is_null", "under"), p)
            if len(s) != 1:
                raise FilterError(p, "give exactly one of in / not_in / is_null / under")
            ((op, arg),) = s.items()
            if op == "is_null":
                if not isinstance(arg, bool):
                    raise FilterError(f"{p}/is_null", "expected true or false")
                out[dim] = {"is_null": arg}
            elif op == "under":
                out[dim] = {"under": _strs(arg, f"{p}/under")[0]}
            else:
                out[dim] = {op: _strs(arg, f"{p}/{op}")}
    return out


def _people(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("assignee", "assignee_in_team", "reporter", "assignment_role", "unassigned"), ptr)
    out: dict[str, Any] = {}
    for k, val in d.items():
        p = f"{ptr}/{k}"
        if k == "assignment_role":
            r = _obj(val, p)
            _only(r, ("role", "person"), p)
            out[k] = {
                "role": _strs(r.get("role"), f"{p}/role")[0],
                "person": _strs(r.get("person"), f"{p}/person")[0],
            }
        elif k == "unassigned":
            if not isinstance(val, bool):
                raise FilterError(p, "expected true or false")
            out[k] = val
        elif k == "assignee_in_team":
            out[k] = _strs(val, p)[0]
        else:
            out[k] = _strs(val, p)
    return out


def _people2(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("reports_to", "kind"), ptr)
    if "reports_to" not in d:
        raise FilterError(f"{ptr}/reports_to", "required")
    kinds = _strs(d.get("kind", ["line", "senior"]), f"{ptr}/kind")
    return {"reports_to": _strs(d["reports_to"], f"{ptr}/reports_to")[0], "kind": kinds}


def _cmp(v: Any, ptr: str, allowed: tuple[str, ...], enum: tuple[str, ...] | None = None) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, allowed, ptr)
    if not d:
        raise FilterError(ptr, "give at least one comparison")
    out: dict[str, Any] = {}
    for op, arg in d.items():
        p = f"{ptr}/{op}"
        if enum is not None:
            vals = _strs(arg, p) if op == "in" else [_strs(arg, p)[0]]
            for x in vals:
                if x not in enum:
                    raise FilterError(p, f"{x!r} is not one of {', '.join(enum)}")
            out[op] = vals if op == "in" else vals[0]
        elif op == "scheme":
            out[op] = _strs(arg, p)[0]
        else:
            out[op] = _num(arg, p)
    return out


def _dates(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("between", "before", "after", "is_null"), ptr)
    if len(d) != 1:
        raise FilterError(ptr, "give exactly one of between / before / after / is_null")
    ((op, arg),) = d.items()
    p = f"{ptr}/{op}"
    if op == "is_null":
        if not isinstance(arg, bool):
            raise FilterError(p, "expected true or false")
        return {op: arg}
    vals = arg if op == "between" else [arg]
    if op == "between" and (not isinstance(arg, list) or len(arg) != 2):
        raise FilterError(p, "expected [from, to]")
    for i, x in enumerate(vals):
        if not isinstance(x, str) or not is_token(x):
            raise FilterError(f"{p}/{i}" if op == "between" else p, f"{x!r} is not a date or relative token")
    return {op: list(vals) if op == "between" else vals[0]}


def _touched(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("since",), ptr)
    s = _strs(d.get("since"), f"{ptr}/since")[0]
    if not is_token(s):
        raise FilterError(f"{ptr}/since", f"{s!r} is not a date or relative token")
    return {"since": s}


def _state(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("category", "in"), ptr)
    out: dict[str, Any] = {}
    if "category" in d:
        cats = _strs(d["category"], f"{ptr}/category")
        for c in cats:
            if c not in STATE_CATEGORIES:
                raise FilterError(f"{ptr}/category", f"{c!r} is not one of {', '.join(STATE_CATEGORIES)}")
        out["category"] = cats
    if "in" in d:
        out["in"] = _strs(d["in"], f"{ptr}/in")
    if not out:
        raise FilterError(ptr, "give category or in")
    return out


def _stage(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("in", "process", "age_gt_expected"), ptr)
    out: dict[str, Any] = {}
    if "in" in d:
        out["in"] = _strs(d["in"], f"{ptr}/in")
    if "process" in d:
        out["process"] = _strs(d["process"], f"{ptr}/process")[0]
    if "age_gt_expected" in d:
        if not isinstance(d["age_gt_expected"], bool):
            raise FilterError(f"{ptr}/age_gt_expected", "expected true or false")
        out["age_gt_expected"] = d["age_gt_expected"]
    if not out:
        raise FilterError(ptr, "give in, process or age_gt_expected")
    return out


def _in(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("in",), ptr)
    return {"in": _strs(d.get("in"), f"{ptr}/in")}


def _tags(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("all", "any", "none"), ptr)
    if not d:
        raise FilterError(ptr, "give all, any or none")
    return {k: _strs(x, f"{ptr}/{k}") for k, x in d.items()}


def _risk(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("open_score_gte", "linked"), ptr)
    out: dict[str, Any] = {}
    if "open_score_gte" in d:
        out["open_score_gte"] = _num(d["open_score_gte"], f"{ptr}/open_score_gte")
    if "linked" in d:
        out["linked"] = _strs(d["linked"], f"{ptr}/linked")[0]
    if not out:
        raise FilterError(ptr, "give open_score_gte or linked")
    return out


def _violations(v: Any, ptr: str) -> dict[str, Any]:
    d = _obj(v, ptr)
    _only(d, ("open", "category", "severity_gte"), ptr)
    out: dict[str, Any] = {}
    if "open" in d:
        if not isinstance(d["open"], bool):
            raise FilterError(f"{ptr}/open", "expected true or false")
        out["open"] = d["open"]
    if "category" in d:
        out["category"] = _strs(d["category"], f"{ptr}/category")
    if "severity_gte" in d:
        sev = _strs(d["severity_gte"], f"{ptr}/severity_gte")[0]
        if sev not in SEVERITIES:
            raise FilterError(f"{ptr}/severity_gte", f"{sev!r} is not one of {', '.join(SEVERITIES)}")
        out["severity_gte"] = sev
    if not out:
        raise FilterError(ptr, "give open, category or severity_gte")
    return out


def _text(v: Any, ptr: str) -> dict[str, Any]:
    if not isinstance(v, str) or not v.strip():
        raise FilterError(ptr, "expected a non-empty string")
    return {"text": v.strip()}


def _bool(v: Any, ptr: str) -> dict[str, Any]:
    if not isinstance(v, bool):
        raise FilterError(ptr, "expected true or false")
    return {"value": v}


_PARSERS: dict[str, Any] = {
    "dims": _dims,
    "people": _people,
    "people2": _people2,
    "priority": lambda v, p: _cmp(v, p, ("gte", "lte", "eq", "in"), PRIORITIES),
    "weight": lambda v, p: _cmp(v, p, ("gte", "lte", "eq", "scheme")),
    "impact": lambda v, p: _cmp(v, p, ("gte", "lte", "eq")),
    "touched": _touched,
    "state": _state,
    "stage": _stage,
    "type": _in,
    "tags": _tags,
    "risk": _risk,
    "violations": _violations,
    "text": _text,
    "project": _in,
    "include_inbox": _bool,
    **{k: _dates for k in DATE_KEYS},
}


def walk(node: Node) -> list[Clause]:
    """All leaf clauses (for the compiler's lookup pass and for tests)."""
    if isinstance(node, Clause):
        return [node]
    if isinstance(node, Not):
        return walk(node.item)
    return [c for n in node.items for c in walk(n)]
