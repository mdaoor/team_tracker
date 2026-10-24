"""Habits (Phase 8 §8.6): nightly detectors over violations and behaviour -> `Habit` rows per subject and window.

A detector returns, per person, `(occurrences, opportunities, linked violation ids)` for a half-open window; the engine turns that
into rate, trend (this window vs the previous one, +-10 % band), score and status. Manager-owned fields (`status`, `coaching_note`)
are never overwritten, except that a new `observed` negative habit becomes `flagged` once its rate crosses the threshold.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    ActivityStatus,
    DailyLogStatus,
    EventSource,
    HabitStatus,
    Polarity,
    PolicyCategory,
    StateCategory,
    TargetType,
    Trend,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person


@dataclass
class Counts:
    occ: int = 0
    opp: int = 0
    linked: list[str] = field(default_factory=list)


Detector = Callable[[AsyncSession, str, datetime, datetime], Awaitable[dict[str, Counts]]]

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
    dict(key="rework", name="Rework", polarity="negative", category="quality",
         formula={"detector": "rework", "numerator": "items that re-entered an earlier stage", "denominator": "items placed in the window", "min_opportunities": 3},
         thresholds={"flag_rate": 0.25, "windows": ["30d", "90d"]}),
]  # fmt: skip


async def ensure_definitions(s: AsyncSession) -> None:
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


# ---------------------------------------------------------------------------------------------------- detectors
def _d(dt: datetime) -> date:
    return dt.astimezone(UTC).date()


async def _violations(
    s: AsyncSession, ws_id: str, policy_key: str, start: datetime, end: datetime
) -> dict[str, list[tuple[str, str]]]:
    """person -> [(violation id, target id)] for violations of `policy_key` detected in [start, end)."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for vid, pid, tid in (
        await s.execute(
            select(m.Violation.id, m.Violation.person_id, m.Violation.target_id)
            .join(m.Policy, m.Policy.id == m.Violation.policy_id)
            .where(
                m.Violation.workspace_id == ws_id,
                # a policy counts for the habit if it *is* that built-in policy or was built from its template, whatever the workspace named it
                (m.Policy.key == policy_key)
                | m.Policy.condition_template_id.in_(
                    select(m.PolicyTemplate.id).where(m.PolicyTemplate.key == policy_key)
                ),
                m.Violation.person_id.is_not(None),
                m.Violation.detected_at >= start,
                m.Violation.detected_at < end,
            )
        )
    ).all():
        if pid is not None:
            out[pid].append((vid, tid))
    return out


async def _items_by_person(s: AsyncSession, ws_id: str, *cond: Any) -> dict[str, list[Any]]:
    rows = (
        await s.execute(
            select(m.WorkItem, m.WorkflowState.category)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(
                m.Project.workspace_id == ws_id,
                m.WorkItem.deleted_at.is_(None),
                m.WorkItem.is_template.is_(False),
                m.WorkItem.assignee_id.is_not(None),
                *cond,
            )
        )
    ).all()
    out: dict[str, list[Any]] = defaultdict(list)
    for it, cat in rows:
        if it.assignee_id is not None:
            out[it.assignee_id].append((it, cat))
    return out


def _violation_detector(policy_key: str, opp: Callable[..., Awaitable[dict[str, int]]]) -> Detector:
    async def run(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, Counts]:
        vs = await _violations(s, ws_id, policy_key, start, end)
        opps = await opp(s, ws_id, start, end)
        return {
            p: Counts(
                len(vs.get(p, [])), max(opps.get(p, 0), len(vs.get(p, []))), [v for v, _ in vs.get(p, [])]
            )
            for p in set(vs) | set(opps)
        }

    return run


async def _opp_done(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, int]:
    its = await _items_by_person(
        s,
        ws_id,
        m.WorkflowState.category == StateCategory.DONE,
        m.WorkItem.actual_end >= _d(start),
        m.WorkItem.actual_end < _d(end) + timedelta(days=1),
    )
    return {p: len(v) for p, v in its.items()}


async def _opp_weighted(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, int]:
    its = await _items_by_person(
        s,
        ws_id,
        m.WorkItem.weight > 1,
        m.WorkflowState.category.in_([StateCategory.ACTIVE, StateCategory.DONE]),
    )
    return {p: len(v) for p, v in its.items()}


async def _opp_active(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, int]:
    its = await _items_by_person(s, ws_id, m.WorkflowState.category == StateCategory.ACTIVE)
    return {p: len(v) for p, v in its.items()}


async def _opp_placements(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, int]:
    rows = (
        await s.execute(
            select(m.WorkItem.assignee_id, func.count())
            .join(
                m.StagePlacement,
                (m.StagePlacement.target_id == m.WorkItem.id)
                & (m.StagePlacement.target_type == TargetType.WORK_ITEM),
            )
            .where(m.WorkItem.assignee_id.is_not(None), m.StagePlacement.exited_at.is_(None))
            .group_by(m.WorkItem.assignee_id)
        )
    ).all()
    return {p: n for p, n in rows if p is not None}


async def d_evidence_consistency(
    s: AsyncSession, ws_id: str, start: datetime, end: datetime
) -> dict[str, Counts]:
    """Positive: every active item has evidence. With no `evidence_gap` violation at all the rate is 1 (also for nobody-active)."""
    vs = await _violations(s, ws_id, "evidence_gap", start, end)
    opp = await _opp_active(s, ws_id, start, end)
    members = [
        r
        for (r,) in (
            await s.execute(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.deleted_at.is_(None)
                )
            )
        ).all()
    ]
    out = {}
    for p in members:
        gaps = len(vs.get(p, []))
        n = max(opp.get(p, 0), gaps)
        out[p] = (
            Counts(n - gaps, n, [v for v, _ in vs.get(p, [])])
            if gaps == 0
            else Counts(-gaps, n, [v for v, _ in vs.get(p, [])])
        )  # negative occ = "not consistent"
    return out


async def d_estimate_drift(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, Counts]:
    its = await _items_by_person(
        s,
        ws_id,
        m.WorkflowState.category == StateCategory.DONE,
        m.WorkItem.actual_end >= _d(start),
        m.WorkItem.actual_end < _d(end) + timedelta(days=1),
        m.WorkItem.estimate_value.is_not(None),
    )
    ids = [it.id for rows in its.values() for it, _ in rows]
    logged: dict[str, float] = {}
    for k in range(0, len(ids), 5000):
        for wid, mins in (
            await s.execute(
                select(m.TimeEntry.work_item_id, func.sum(m.TimeEntry.minutes))
                .where(m.TimeEntry.work_item_id.in_(ids[k : k + 5000]), m.TimeEntry.deleted_at.is_(None))
                .group_by(m.TimeEntry.work_item_id)
            )
        ).all():
            logged[wid] = float(mins or 0) / 60
    out: dict[str, Counts] = {}
    for p, rows in its.items():
        c = Counts()
        for it, _ in rows:
            est = float(it.estimate_value) * (
                8 if it.estimate_unit.value == "days" else 1 if it.estimate_unit.value == "hours" else 0
            )
            if est <= 0 or logged.get(it.id, 0) <= 0:
                continue
            c.opp += 1
            c.occ += abs(est - logged[it.id]) / est > 0.25
        out[p] = c
    return out


async def d_context_switching(
    s: AsyncSession, ws_id: str, start: datetime, end: datetime
) -> dict[str, Counts]:
    rows = (
        await s.execute(
            select(
                m.TimeEntry.person_id, m.TimeEntry.date, func.count(func.distinct(m.TimeEntry.work_item_id))
            )
            .where(
                m.TimeEntry.date >= _d(start),
                m.TimeEntry.date < _d(end) + timedelta(days=1),
                m.TimeEntry.deleted_at.is_(None),
            )
            .group_by(m.TimeEntry.person_id, m.TimeEntry.date)
        )
    ).all()
    out: dict[str, Counts] = defaultdict(Counts)
    for p, _, n in rows:
        out[p].opp += 1
        out[p].occ += n > 5
    return dict(out)


async def d_long_sessions(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, Counts]:
    out: dict[str, Counts] = defaultdict(Counts)
    for a in (
        await s.scalars(
            select(m.Activity)
            .join(m.Project, m.Project.id == m.Activity.project_id)
            .where(
                m.Project.workspace_id == ws_id, m.Activity.started_at >= start, m.Activity.started_at < end
            )
        )
    ).all():
        stop = a.ended_at or end
        out[a.person_id].opp += 1
        out[a.person_id].occ += (stop - a.started_at) > timedelta(hours=4) and (
            a.status != ActivityStatus.ACTIVE or True
        )
    return dict(out)


async def d_log_consistency(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, Counts]:
    days = sum(1 for k in range((_d(end) - _d(start)).days) if (_d(start) + timedelta(days=k)).weekday() < 5)
    rows = (
        await s.execute(
            select(m.DailyLog.person_id, func.count())
            .where(
                m.DailyLog.workspace_id == ws_id,
                m.DailyLog.date >= _d(start),
                m.DailyLog.date < _d(end),
                m.DailyLog.status.in_([DailyLogStatus.CONFIRMED, DailyLogStatus.REVIEWED]),
            )
            .group_by(m.DailyLog.person_id)
        )
    ).all()
    return {p: Counts(min(n, days), days) for p, n in rows}


async def d_early_delivery(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, Counts]:
    its = await _items_by_person(
        s,
        ws_id,
        m.WorkflowState.category == StateCategory.DONE,
        m.WorkItem.actual_end >= _d(start),
        m.WorkItem.actual_end < _d(end) + timedelta(days=1),
    )
    return {
        p: Counts(
            sum(1 for it, _ in rows if it.planned_end and _d(it.actual_end) < it.planned_end), len(rows)
        )
        for p, rows in its.items()
    }


async def d_unassigned_inbox_age(
    s: AsyncSession, ws_id: str, start: datetime, end: datetime
) -> dict[str, Counts]:
    rows = (
        await s.execute(
            select(m.WorkItem.reporter_id, m.WorkItem.created_at)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .where(
                m.Project.workspace_id == ws_id,
                m.WorkItem.is_provisional.is_(True),
                m.WorkItem.deleted_at.is_(None),
            )
        )
    ).all()
    out: dict[str, Counts] = defaultdict(Counts)
    for p, created in rows:
        out[p].opp += 1
        out[p].occ += (end - created) > timedelta(days=7)
    return dict(out)


async def d_rework(s: AsyncSession, ws_id: str, start: datetime, end: datetime) -> dict[str, Counts]:
    rows = (
        await s.execute(
            select(m.WorkItem.id, m.WorkItem.assignee_id, m.Stage.position, m.StagePlacement.entered_at)
            .join(
                m.StagePlacement,
                (m.StagePlacement.target_id == m.WorkItem.id)
                & (m.StagePlacement.target_type == TargetType.WORK_ITEM),
            )
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .where(
                m.WorkItem.assignee_id.is_not(None),
                m.StagePlacement.entered_at >= start,
                m.StagePlacement.entered_at < end,
            )
            .order_by(m.WorkItem.id, m.StagePlacement.entered_at)
        )
    ).all()
    per: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for iid, pid, pos, _ in rows:
        if pid is not None:
            per[iid].append((pid, pos))
    out: dict[str, Counts] = defaultdict(Counts)
    for seq in per.values():
        pid = seq[0][0]
        out[pid].opp += 1
        peak = -1
        for _, pos in seq:
            if pos < peak:
                out[pid].occ += 1
                break
            peak = max(peak, pos)
    return dict(out)


def detectors() -> dict[str, Detector]:
    return {
        "late_finish": _violation_detector("overrun", _opp_done),
        "no_estimates": _violation_detector("no_estimate_weighted", _opp_weighted),
        "evidence_gap": _violation_detector("evidence_gap", _opp_active),
        "stage_stalls": _violation_detector("stuck_in_stage", _opp_placements),
        "evidence_consistency": d_evidence_consistency,
        "estimate_drift": d_estimate_drift,
        "context_switching": d_context_switching,
        "long_sessions": d_long_sessions,
        "log_consistency": d_log_consistency,
        "early_delivery": d_early_delivery,
        "unassigned_inbox_age": d_unassigned_inbox_age,
        "rework": d_rework,
    }


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
    n, unit = int(w[:-1]), w[-1]
    return n * {"d": 1, "w": 7}[unit]


@dataclass
class HabitStats:
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
