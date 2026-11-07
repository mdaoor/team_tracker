"""Write docs/governance/context-schema-v1.md and templates.md from the declarations in `tt_cel` (a test fails on drift)."""

from __future__ import annotations

from pathlib import Path

import tt_cel
from tt_cel.schema import OBJECTS, ROOTS

DOCS = Path(__file__).resolve().parents[1] / "docs" / "governance"


def schema_md() -> str:
    out = [
        "# Policy context schema v1",
        "",
        "_Generated from `packages/cel_policies/src/tt_cel/schema.py` by `tools/gen_governance_docs.py`; do not edit by hand._",
        "",
        "A policy condition is one [CEL](https://github.com/google/cel-spec) expression over these roots. It is type-checked when the",
        "policy is saved: an unknown root, field or function, or a clear type mismatch (`item.priority > 3`), is rejected with the offending",
        "field named. A policy stores `context_schema_version`, so a later v2 never changes how a v1 policy is checked or evaluated.",
        "",
        "Dates the plan keeps as days (`planned_end`, `actual_end`, `due_date`, `date` of a daily log) are timestamps at UTC midnight; compare",
        "them with each other, not with `now`. `x == null` / `x != null` never raise. Expressions are limited to 4 KB and 400 syntax nodes.",
        "",
        "## Roots",
        "",
        "| root | type | what it is |",
        "|---|---|---|",
    ]
    desc = {
        "item": "the work item (also present for placement, activity and violation targets that have one)",
        "activity": "the work session being evaluated",
        "person": "the accountable person (assignee, activity owner, log owner, or the person target)",
        "project": "the item's project, or the project target",
        "stage": "the item's current stage (`stage.wip_count()` counts open items of this project in the stage)",
        "placement": "the item's current stage placement",
        "event": "the event that triggered an `on_event` evaluation; blank for scheduled runs",
        "daily_log": "the daily log being evaluated",
        "metrics": 'namespace of aggregate functions (`metrics.minutes_logged(person.id, "today")`)',
        "now": "the evaluation time (the `as_of` of a replay)",
    }
    for r, t in ROOTS.items():
        out.append(f"| `{r}` | `{t}` | {desc[r]} |")
    for name, fields in OBJECTS.items():
        if not fields:
            continue
        out += ["", f"## `{name}`", "", "| field | type |", "|---|---|"]
        out += [f"| `{k}` | `{v}` |" for k, v in fields.items()]
    out += [
        "",
        "## Registered functions (Tier 3)",
        "",
        "Heavy lookups are implemented once in server code and read facts the engine preloads per batch (one grouped query per fact group, never one per item).",
        "",
        "| call | returns | what it does |",
        "|---|---|---|",
    ]
    for f in tt_cel.FUNCTIONS:
        recv = f"{f.namespace}." if f.namespace else (f"<{f.receiver.lower()}>." if f.receiver else "")
        args = ", ".join(a.rstrip("?") + ("?" if a.endswith("?") else "") for a in f.args)
        out.append(f"| `{recv}{f.name}({args})` | `{f.returns}` | {f.doc} |")
    out += [
        "",
        "Also available: `size`, `has`, `string`, `int`, `double`, `bool`, `duration`, `timestamp`, `contains`, `startsWith`, `endsWith`, `matches`, and the macros `exists`, `all`, `exists_one`, `map`, `filter`.",
        "",
    ]
    return "\n".join(out)


def templates_md() -> str:
    out = [
        "# Built-in policy templates",
        "",
        "_Generated from `packages/cel_policies/src/tt_cel/templates/builtin/*.yaml` by `tools/gen_governance_docs.py`._",
        "",
        "Tier 1 of the policy design: a manager picks a template and fills in its parameters; the template compiles to CEL (tier 2) which is",
        "evaluated against the context schema (see `context-schema-v1.md`). Raw CEL needs `policy.admin`. Parameter syntax inside a template:",
        "`{name}` (typed literal), `{name|expr}` (fall back to an expression when unset), `<<?name ... ?>>` (text only when set).",
        "",
    ]
    for k, t in sorted(tt_cel.builtin_templates().items()):
        out += [
            f"## `{k}` — {t.name}",
            "",
            t.description,
            "",
            f"* category `{t.category}`, severity `{t.severity}`, target `{t.target}`, trigger `{t.trigger}`"
            + (f" (cron `{t.schedule}`)" if t.schedule else "")
            + (f", events `{', '.join(t.event_types)}`" if t.event_types else "")
            + (f", one violation per `{t.grouping}`" if t.grouping else ""),
        ]
        if t.candidate_filter:
            out.append(f"* candidate filter: `{t.candidate_filter}`")
        out += ["", "| parameter | type | default | bounds | meaning |", "|---|---|---|---|---|"]
        out += [
            f"| `{p.name}` | {p.type} | {p.default if p.default is not None else '—'} | {p.min if p.min is not None else ''}{'..' if p.min is not None or p.max is not None else ''}{p.max if p.max is not None else ''} | {p.help} |"
            for p in t.params
        ] or ["| — | | | | no parameters |"]
        out += ["", "```cel", t.cel, "```", "", f"Violation text: `{t.detail}` — e.g. _{t.example}_", ""]
    return "\n".join(out)


def render() -> dict[str, str]:
    return {"context-schema-v1.md": schema_md(), "templates.md": templates_md()}


if __name__ == "__main__":
    for n, text in render().items():
        (DOCS / n).write_text(text)
        print("wrote", DOCS / n)
