"""Tier-3 registered functions: pure lookups over facts the engine preloaded (no SQL, no I/O at evaluation time).

`bind(facts)` returns the callables celpy needs. The `facts` object is duck-typed (see `tt_server.governance.facts.Facts`), which keeps
this package free of the server. Method-style calls `x.f(a)` arrive as `f(x, a)`.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from celpy import celtypes
from celpy.evaluation import bool_eq

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ts(dt: datetime | None) -> celtypes.TimestampType:
    return celtypes.TimestampType(dt or EPOCH)


def bind(facts: Any) -> dict[str, Callable[..., Any]]:
    def iid(m: Any) -> str:
        return str(m["id"])

    def evidence_last_at(item: Any) -> Any:
        return _ts(facts.evidence_last.get(iid(item)))

    def evidence_count(item: Any, kind: Any = None) -> Any:
        return celtypes.IntType(
            facts.evidence_count.get((iid(item), str(kind) if kind is not None else None), 0)
        )

    def hours_logged(item: Any, window: Any) -> Any:
        return celtypes.DoubleType(facts.hours.get((iid(item), str(window)), 0.0))

    def reopen_count(item: Any) -> Any:
        return celtypes.IntType(facts.reopens.get(iid(item), 0))

    def days_in_stage(item: Any) -> Any:
        return celtypes.IntType(facts.stage_days.get(iid(item), 0))

    def children_count(item: Any) -> Any:
        return celtypes.IntType(facts.children.get(iid(item), 0))

    def open_violations(item: Any, category: Any = None) -> Any:
        return celtypes.IntType(
            facts.violations.get((iid(item), str(category) if category is not None else None), 0)
        )

    def missing_required_dimensions(item: Any) -> Any:
        return celtypes.ListType([celtypes.StringType(k) for k in facts.missing_dims.get(iid(item), [])])

    def wip_count(stage: Any) -> Any:
        return celtypes.IntType(facts.wip.get(str(stage.get("_wip_key", "")), 0))

    def minutes_logged(_ns: Any, person_id: Any, window: Any) -> Any:
        return celtypes.IntType(facts.minutes.get((str(person_id), str(window)), 0))

    def distinct_items(_ns: Any, person_id: Any, window: Any) -> Any:
        return celtypes.IntType(facts.distinct.get((str(person_id), str(window)), 0))

    def active_items(_ns: Any, person_id: Any) -> Any:
        return celtypes.IntType(facts.active.get(str(person_id), 0))

    def wip(_ns: Any, project_key: Any, stage: Any = None) -> Any:
        return celtypes.IntType(facts.wip.get(f"{project_key}|{stage if stage is not None else ''}", 0))

    def overrun_rate(_ns: Any, project_key: Any, window: Any) -> Any:
        return celtypes.DoubleType(facts.overrun.get((str(project_key), str(window)), 0.0))

    def business_days(n: Any) -> Any:
        return celtypes.DurationType(timedelta(days=math.ceil(int(n) * 7 / 5)))

    def _eq(a: Any, b: Any) -> Any:
        """CEL says `x == null` is simply false for a non-null `x`; celpy raises instead. Policies compare against null a lot."""
        if a is None or b is None:
            return celtypes.BoolType(a is None and b is None)
        return bool_eq(a, b)

    def _ne(a: Any, b: Any) -> Any:
        return celtypes.BoolType(not _eq(a, b))

    ops: dict[str, Callable[..., Any]] = {"_==_": _eq, "_!=_": _ne}
    return ops | {f.__name__: f for f in (
        evidence_last_at, evidence_count, hours_logged, reopen_count, days_in_stage, children_count, open_violations,
        missing_required_dimensions, wip_count, minutes_logged, distinct_items, active_items, wip, overrun_rate, business_days,
    )}  # fmt: skip
