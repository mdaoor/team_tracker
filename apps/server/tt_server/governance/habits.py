"""Habits (Phase 8 §8.6): nightly detectors over violations and behaviour -> `Habit` rows per subject and window.

A detector returns, per person, `(occurrences, opportunities, linked violation ids)` for a half-open window; the engine turns that
into rate, trend (this window vs the previous one, +-10 % band), score and status. Manager-owned fields (`status`, `coaching_note`)
are never overwritten, except that a new `observed` negative habit becomes `flagged` once its rate crosses the threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    EventSource,
    HabitStatus,
    Polarity,
    PolicyCategory,
    TargetType,
    Trend,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.governance.habit_detectors import (
    Counts,
    Detector,
    d_context_switching,
    d_early_delivery,
    d_estimate_drift,
    d_evidence_consistency,
    d_learning_consistency,
    d_log_consistency,
    d_long_sessions,
    d_rework,
    d_unassigned_inbox_age,
    detectors,
)

DEFINITIONS: list[dict[str, Any]] = [
    dict(key="late_finish", name="Finishes late", polarity="negative", category="time",
         formula={"detector": "late_finish", "numerator": "violations:overrun", "denominator": "done items in the window", "min_opportunities": 1},
         thresholds={"flag_rate": 0.3, "windows": ["30d", "90d"]}),
    dict(key="estimate_drift", name="Estimates drift from actuals", polarity="negative", category="planning",
         formula={"detector": "estimate_drift", "numerator": "done items whose logged hours differ from the estimate by more than 25 %", "denominator": "done items with an estimate and logged time", "min_opportunities": 3},
         thresholds={"flag_rate": 0.4, "windows": ["30d", "90d"]}),
    dict(key="no_estimates", name="Starts weighted work without estimates", polarity="negative", category="planning",
         formula={"detector": "no_estimates", "numerator": "violations:no_estimate_weighted", "denominator": "weighted items active or done in the window", "min_opportunities": 1},
         thresholds={"flag_rate": 0.3, "windows": ["30d", "90d"]}),
    dict(key="evidence_gap", name="Active work without evidence", polarity="negative", category="quality",
         formula={"detector": "evidence_gap", "numerator": "violations:evidence_gap", "denominator": "active items", "min_opportunities": 1},
         thresholds={"flag_rate": 0.4, "windows": ["30d", "90d"]}),
    dict(key="evidence_consistency", name="Evidence on active work", polarity="positive", category="quality",
         formula={"detector": "evidence_consistency", "numerator": "active items with evidence", "denominator": "active items (no `evidence_gap` violations at all = rate 1)", "min_opportunities": 0},
         thresholds={"windows": ["30d", "90d"]}),
    dict(key="context_switching", name="Context switching", polarity="negative", category="collaboration",
         formula={"detector": "context_switching", "numerator": "days with more than 5 distinct items", "denominator": "days with logged time", "min_opportunities": 5},
         thresholds={"flag_rate": 0.4, "windows": ["30d"]}),
    dict(key="long_sessions", name="Very long sessions", polarity="negative", category="wellbeing",
         formula={"detector": "long_sessions", "numerator": "activities longer than 4 h", "denominator": "activities", "min_opportunities": 3},
         thresholds={"flag_rate": 0.25, "windows": ["30d"]}),
    dict(key="log_consistency", name="Confirms the daily log", polarity="positive", category="collaboration",
         formula={"detector": "log_consistency", "numerator": "confirmed or reviewed daily logs", "denominator": "working days in the window", "min_opportunities": 3},
         thresholds={"windows": ["30d"]}),
    dict(key="early_delivery", name="Delivers early", polarity="positive", category="time",
         formula={"detector": "early_delivery", "numerator": "done items finished before the planned end", "denominator": "done items in the window", "min_opportunities": 3},
         thresholds={"windows": ["30d", "90d"]}),
    dict(key="stage_stalls", name="Items stall in stages", polarity="negative", category="process",
         formula={"detector": "stage_stalls", "numerator": "violations:stuck_in_stage", "denominator": "open stage placements", "min_opportunities": 1},
         thresholds={"flag_rate": 0.4, "windows": ["30d", "90d"]}),
    dict(key="unassigned_inbox_age", name="Inbox items left to age", polarity="negative", category="process",
         formula={"detector": "unassigned_inbox_age", "numerator": "provisional items older than 7 days", "denominator": "provisional items", "min_opportunities": 3},
         thresholds={"flag_rate": 0.5, "windows": ["30d"]}),
    dict(key="learning_consistency", name="Keeps learning every week", polarity="positive", category="wellbeing",
         formula={"detector": "learning_consistency", "numerator": "weeks in the window with time logged on #development work", "denominator": "weeks in the window", "min_opportunities": 2},
         thresholds={"windows": ["30d", "90d"]}),
    dict(key="rework", name="Rework", polarity="negative", category="quality",
         formula={"detector": "rework", "numerator": "items that re-entered an earlier stage", "denominator": "items placed in the window", "min_opportunities": 3},
         thresholds={"flag_rate": 0.25, "windows": ["30d", "90d"]}),
]  # fmt: skip


async def ensure_definitions(s: AsyncSession) -> None:
    """Upsert the built-in habit definitions into the global (workspace-less) rows."""
    have = {
        r.key: r
        for r in (
            await s.scalars(select(m.HabitDefinition).where(m.HabitDefinition.workspace_id.is_(None)))
        ).all()
    }
    for d in DEFINITIONS:
        row = have.get(d["key"])
        vals = dict(
            name=d["name"],
            polarity=Polarity(d["polarity"]),
            category=PolicyCategory(d["category"]),
            formula=d["formula"],
            thresholds=d["thresholds"],
        )
        if row is None:
            s.add(m.HabitDefinition(id=new_id(), key=d["key"], workspace_id=None, **vals))
        else:
            for k, v in vals.items():
                if getattr(row, k) != v:
                    setattr(row, k, v)


# ---------------------------------------------------------------------------------------------------- computation
def _rate(c: Counts, positive_inverse: bool) -> Decimal:
    if positive_inverse:
        return Decimal("1") if c.occ >= 0 and c.opp == 0 else Decimal(max(c.occ, 0)) / Decimal(max(c.opp, 1))
    return Decimal(c.occ) / Decimal(max(c.opp, 1))


def _by_subject(
    counts: Mapping[str, Counts], team_of: dict[str, str]
) -> dict[tuple[TargetType, str], Counts]:
    """Per-person counts plus their team totals, keyed by (subject type, id)."""
    out: dict[tuple[TargetType, str], Counts] = {}
    for pid, c in counts.items():
        if pid is None:
            continue
        out[(TargetType.PERSON, pid)] = c
        if (team_id := team_of.get(pid)) is not None:
            t = out.setdefault((TargetType.TEAM, team_id), Counts())
            t.occ += c.occ
            t.opp += c.opp
            t.linked += c.linked
    return out


def _trend(polarity: Polarity, now_rate: Decimal, prev_rate: Decimal | None) -> Trend:
    if prev_rate is None:
        return Trend.STABLE
    base = max(prev_rate, Decimal("0.0001"))
    change = (now_rate - prev_rate) / base
    if abs(change) <= Decimal("0.10"):
        return Trend.STABLE
    better = change < 0 if polarity == Polarity.NEGATIVE else change > 0
    return Trend.IMPROVING if better else Trend.WORSENING


def window_days(w: str) -> int:
    """Convert a window such as '4w' or '30d' to a number of days."""
    n, unit = int(w[:-1]), w[-1]
    return n * {"d": 1, "w": 7}[unit]


@dataclass
class HabitStats:
    """Counters of a habit recompute: rows upserted, flagged and removed."""

    upserted: int = 0
    flagged: int = 0
    removed: int = 0


async def compute(uow: UnitOfWork, ws: m.Workspace, *, now: datetime | None = None) -> HabitStats:
    """Recompute every habit of the workspace (people and teams) for every window of every definition."""
    s = uow.session
    now = now or utcnow()
    await ensure_definitions(s)
    await s.flush()
    sysp = await system_person(s)
    uow.context.update(actor_id=sysp.id)
    defs = (
        await s.scalars(
            select(m.HabitDefinition).where(
                (m.HabitDefinition.workspace_id == ws.id) | m.HabitDefinition.workspace_id.is_(None)
            )
        )
    ).all()
    by_key: dict[str, m.HabitDefinition] = {}
    for d in sorted(defs, key=lambda x: x.workspace_id is not None):  # workspace definitions shadow built-ins
        by_key[d.key] = d
    team_of: dict[str, str] = {}
    for pid, tid in (
        await s.execute(
            select(m.Membership.person_id, m.Membership.team_id)
            .where(m.Membership.left_at.is_(None), m.Membership.deleted_at.is_(None))
            .order_by(m.Membership.joined_at)
        )
    ).all():
        team_of[pid] = tid
    existing = {
        (h.subject_type, h.subject_id, h.key, h.window): h
        for h in (
            await s.scalars(
                select(m.Habit).where(m.Habit.workspace_id == ws.id, m.Habit.deleted_at.is_(None))
            )
        ).all()
    }
    stats = HabitStats()
    seen: set[tuple[Any, ...]] = set()
    dets = detectors()
    for key, d in by_key.items():
        det = dets.get(d.formula.get("detector", key))
        if det is None:
            continue
        thr = d.thresholds or {}
        minimum = int(d.formula.get("min_opportunities", 1))
        for w in thr.get("windows", ["30d"]):
            span = timedelta(days=window_days(w))
            cur = await det(s, ws.id, now - span, now + timedelta(seconds=1))
            prev = await det(s, ws.id, now - 2 * span, now - span)
            subjects = _by_subject(cur, team_of)
            previous = _by_subject(
                prev, team_of
            )  # team trends compare team totals with team totals (review M6)
            for (st, sid), c in subjects.items():
                inverse = d.polarity == Polarity.POSITIVE and key == "evidence_consistency"
                bad = c.occ < 0
                if bad or c.opp < minimum or (c.occ == 0 and not inverse):
                    continue  # nothing to report (negative habit with no occurrences, or too few opportunities)
                rate = _rate(c, inverse)
                p = previous.get((st, sid))
                prate = _rate(p, inverse) if p is not None and p.opp else None
                row = existing.get((st, sid, key, w))
                trend = _trend(d.polarity, rate, prate)
                score = min(Decimal("1"), rate)
                occurrences = max(c.occ, 0)
                seen.add((st, sid, key, w))
                if row is None:
                    row = m.Habit(
                        id=new_id(), workspace_id=ws.id, subject_type=st, subject_id=sid, key=key, polarity=d.polarity, window=w, occurrences=occurrences,
                        rate=rate.quantize(Decimal("0.0001")), trend=trend, score=score.quantize(Decimal("0.0001")), first_seen_at=now, last_seen_at=now,
                        linked_violation_ids=c.linked, status=HabitStatus.OBSERVED, category=d.category,
                    )  # fmt: skip
                    s.add(row)
                    changed = True
                else:
                    new_rate = rate.quantize(Decimal("0.0001"))
                    changed = (row.occurrences, row.rate, row.trend, row.linked_violation_ids) != (
                        occurrences,
                        new_rate,
                        trend,
                        c.linked,
                    )
                    if changed:  # an unchanged habit is not touched at all: no write, no event
                        row.occurrences, row.rate, row.trend, row.score = (
                            occurrences,
                            new_rate,
                            trend,
                            score.quantize(Decimal("0.0001")),
                        )
                        row.last_seen_at, row.linked_violation_ids, row.category, row.polarity = (
                            now,
                            c.linked,
                            d.category,
                            d.polarity,
                        )
                flag = (
                    d.polarity == Polarity.NEGATIVE
                    and rate >= Decimal(str(thr.get("flag_rate", 0.3)))
                    and row.status == HabitStatus.OBSERVED
                )
                if flag:
                    row.status = HabitStatus.FLAGGED
                    stats.flagged += 1
                if changed or flag:
                    await s.flush()
                    emit(
                        uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=st, entity_id=sid,
                        event_type="habit.flagged" if flag else "habit.updated",
                        payload={"after": {"habit": key, "window": w, "rate": float(rate), "trend": trend.value, "status": row.status.value}},
                        source=EventSource.RULE, occurred_at=now,
                    )  # fmt: skip
                    stats.upserted += 1
    # a habit that no longer shows up (condition cleared, or too few opportunities) is dropped unless a manager is working on it
    for k, h in existing.items():
        if k not in seen and h.status in (HabitStatus.OBSERVED, HabitStatus.FLAGGED):
            await s.delete(h)
            stats.removed += 1
    if stats.removed and not stats.upserted:
        emit(uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.PERSON, entity_id=sysp.id, event_type="habit.updated", payload={"after": {"removed": stats.removed}}, source=EventSource.RULE, occurred_at=now)  # fmt: skip
    return stats


async def series(
    s: AsyncSession, ws_id: str, habit: m.Habit, *, points: int = 8, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Rolling rate of one habit for the sparkline: the habit's own window, re-evaluated every 7 days going back `points` steps.

    Oldest first. Uses the same detector and rate rule as `compute`, so the last point equals the stored habit (up to the minute)."""
    now = now or utcnow()
    det = detectors().get(habit.key)
    d = await s.scalar(
        select(m.HabitDefinition).where(
            m.HabitDefinition.key == habit.key,
            (m.HabitDefinition.workspace_id == ws_id) | m.HabitDefinition.workspace_id.is_(None),
        ).order_by(m.HabitDefinition.workspace_id.is_(None))
    )  # fmt: skip
    if det is None or d is None:
        return []
    det = detectors().get(d.formula.get("detector", habit.key), det)
    team_of: dict[str, str] = {}
    for pid, tid in (
        await s.execute(
            select(m.Membership.person_id, m.Membership.team_id)
            .where(m.Membership.left_at.is_(None), m.Membership.deleted_at.is_(None))
            .order_by(m.Membership.joined_at)
        )
    ).all():
        team_of[pid] = tid
    span = timedelta(days=window_days(habit.window))
    inverse = d.polarity == Polarity.POSITIVE and habit.key == "evidence_consistency"
    out: list[dict[str, Any]] = []
    for i in range(points - 1, -1, -1):
        end = now - timedelta(days=7 * i)
        counts = _by_subject(await det(s, ws_id, end - span, end + timedelta(seconds=1)), team_of)
        c = counts.get((habit.subject_type, habit.subject_id))
        out.append(
            {
                "at": end.isoformat(),
                "rate": float(_rate(c, inverse)) if c is not None and c.opp > 0 else None,
                "occurrences": max(c.occ, 0) if c else 0,
                "opportunities": c.opp if c else 0,
            }
        )
    return out


# The detectors moved to `habit_detectors`; the old import path keeps working.
__all__ = [
    "Counts",
    "Detector",
    "HabitStats",
    "compute",
    "d_context_switching",
    "d_early_delivery",
    "d_estimate_drift",
    "d_evidence_consistency",
    "d_learning_consistency",
    "d_log_consistency",
    "d_long_sessions",
    "d_rework",
    "d_unassigned_inbox_age",
    "detectors",
    "ensure_definitions",
    "series",
    "window_days",
]
