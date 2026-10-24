"""Policy context schema v1: the roots a CEL expression may read, with declared field types.

Types are strings: scalars (`string int double bool timestamp duration`), `dyn` (anything), `list<T>`, `map<string,T>` and the
names of the objects below. `dyn` fields are not checked below the root. A policy stores `context_schema_version`, so adding v2
never changes how v1 policies are checked or evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERSION = 1


@dataclass(frozen=True)
class Func:
    name: str
    receiver: (
        str | None
    )  # object type for `x.name(...)`, None for a global or a `metrics.` namespace function
    args: tuple[str, ...]
    returns: str
    doc: str
    namespace: str | None = None  # `metrics`
    needs: str | None = (
        None  # fact group the engine preloads before evaluating (see tt_server.governance.facts)
    )


OBJECTS: dict[str, dict[str, str]] = {
    "Item": {
        "id": "string",
        "key": "string",
        "title": "string",
        "project": "Project",
        "type": "KeyOnly",
        "state": "State",
        "priority": "string",
        "priority_source": "string",
        "weight": "double",
        "impact": "int",
        "estimate_value": "double",
        "estimate_unit": "string",
        "remaining_value": "double",
        "planned_start": "timestamp",
        "planned_end": "timestamp",
        "due_date": "timestamp",
        "actual_start": "timestamp",
        "actual_end": "timestamp",
        "assignee": "Assignee",
        "team": "Team",
        "tags": "list<string>",
        "risks": "list<Risk>",
        "stage": "Stage",
        "placement": "Placement",
        "classification": "map<string,dyn>",
        "is_provisional": "bool",
        "created_at": "timestamp",
        "closed_at": "timestamp",
    },
    "Project": {
        "id": "string",
        "key": "string",
        "methodology": "string",
        "status": "string",
        "planned_end": "timestamp",
        "team": "string",
        "product": "string",
    },
    "KeyOnly": {"key": "string"},
    "State": {"key": "string", "category": "string"},
    "Assignee": {"id": "string", "handle": "string", "team": "string"},
    "Team": {"slug": "string"},
    "Risk": {"key": "string", "score": "int", "status": "string"},
    "Stage": {
        "key": "string",
        "category": "string",
        "expected_duration_days": "int",
        "wip_limit": "int",
        "process": "string",
    },
    "Placement": {
        "entered_at": "timestamp",
        "exited_at": "timestamp",
        "process": "string",
        "stage": "string",
    },
    "Activity": {
        "id": "string",
        "kind": "string",
        "status": "string",
        "started_at": "timestamp",
        "ended_at": "timestamp",
        "duration": "duration",
        "person": "string",
        "item": "string",
    },
    "Person": {
        "id": "string",
        "handle": "string",
        "team": "string",
        "capacity_hours_per_week": "double",
        "time_zone": "string",
    },
    "Event": {"type": "string", "source": "string", "occurred_at": "timestamp", "payload": "map<string,dyn>"},
    "DailyLog": {"date": "timestamp", "status": "string", "minutes_total": "int", "item_ids": "list<string>"},
    "Metrics": {},
}

ROOTS: dict[str, str] = {
    "item": "Item", "activity": "Activity", "person": "Person", "project": "Project", "stage": "Stage", "placement": "Placement",
    "event": "Event", "daily_log": "DailyLog", "metrics": "Metrics", "now": "timestamp",
}  # fmt: skip

# Tier 3: registered functions. The docstrings become the template editor's help and docs/governance/context-schema-v1.md.
FUNCTIONS: tuple[Func, ...] = (
    Func("evidence_last_at", "Item", (), "timestamp", "When the newest evidence on the item was captured (the epoch if there is none).", needs="evidence"),
    Func("evidence_count", "Item", ("string?",), "int", "Number of evidence rows on the item, optionally of one kind (`pr`, `note`, ...).", needs="evidence"),
    Func("hours_logged", "Item", ("string",), "double", "Hours logged on the item in a window (`today`, `7d`, `30d`).", needs="hours"),
    Func("reopen_count", "Item", (), "int", "How many times the item left a done state.", needs="reopens"),
    Func("days_in_stage", "Item", (), "int", "Whole days since the item entered its current stage.", needs="placement"),
    Func("children_count", "Item", (), "int", "Direct children of the item.", needs="children"),
    Func("open_violations", "Item", ("string?",), "int", "Open violations on the item, optionally of one category.", needs="violations"),
    Func("missing_required_dimensions", "Item", (), "list<string>", "Keys of dimensions required for the item's stage that have no confirmed value.", needs="dimensions"),
    Func("wip_count", "Stage", (), "int", "Open items in this stage within the policy's scope (the project for project-grouped policies).", needs="wip"),
    Func("minutes_logged", None, ("string", "string"), "int", "Minutes a person logged in a window (`today`, `7d`).", namespace="metrics", needs="minutes"),
    Func("active_items", None, ("string",), "int", "Items a person currently has an active session on.", namespace="metrics", needs="active"),
    Func("distinct_items", None, ("string", "string"), "int", "Distinct items a person worked on in a window.", namespace="metrics", needs="minutes"),
    Func("wip", None, ("string", "string?"), "int", "Open items of a project, optionally in one stage.", namespace="metrics", needs="wip"),
    Func("overrun_rate", None, ("string", "string"), "double", "Share of items finished late in a project in a window.", namespace="metrics", needs="overrun"),
    Func("business_days", None, ("int",), "duration", "A duration of n working days (calendar days x 7/5, rounded up): `now - business_days(3)`."),
)  # fmt: skip

BUILTINS = frozenset(
    {
        "size", "has", "string", "int", "double", "bool", "duration", "timestamp", "type", "dyn", "contains", "startsWith", "endsWith",
        "matches", "exists", "all", "map", "filter", "exists_one", "getFullYear", "getMonth", "getDate", "getDayOfWeek", "getHours",
    }
)  # fmt: skip


@dataclass
class Schema:
    version: int = VERSION
    objects: dict[str, dict[str, str]] = field(default_factory=lambda: OBJECTS)
    roots: dict[str, str] = field(default_factory=lambda: ROOTS)
    functions: tuple[Func, ...] = FUNCTIONS


SCHEMA_V1 = Schema()
