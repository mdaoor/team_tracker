"""Habit detectors: per-person (occurrences, opportunities) over a half-open window, from violations and behaviour."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import (
    ActivityStatus,
    DailyLogStatus,
    StateCategory,
    TargetType,
)

from tt_server.db import models as m


@dataclass
class Counts:
    """Occurrences, opportunities and linked evidence of one habit for one subject."""

    occ: int = 0
    opp: int = 0
    linked: list[str] = field(default_factory=list)


Detector = Callable[[AsyncSession, str, datetime, datetime], Awaitable[dict[str, Counts]]]


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
    """Detector: finished items whose actuals drift from their estimate."""
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
    """Detector: days on which a person touched too many items."""
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
    """Detector: activities that ran past the long-session threshold."""
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
    """Detector: weekdays with a daily log versus weekdays in the window."""
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
    """Detector: items finished before their due date."""
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
    """Detector: how long reported items wait in the inbox unassigned."""
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
    """Detector: items that moved back to an earlier stage."""
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


async def d_learning_consistency(
    s: AsyncSession, ws_id: str, start: datetime, end: datetime
) -> dict[str, Counts]:
    """Positive habit (Phase 15): the share of weeks in the window with any time on `#development` work."""
    weeks = max(1, -(-(_d(end) - _d(start)).days // 7))
    rows = (
        await s.execute(
            select(m.TimeEntry.person_id, m.TimeEntry.date)
            .join(
                m.TagLink,
                (m.TagLink.target_id == m.TimeEntry.work_item_id)
                & (m.TagLink.target_type == TargetType.WORK_ITEM),
            )
            .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
            .where(
                m.TimeEntry.date >= _d(start),
                m.TimeEntry.date < _d(end) + timedelta(days=1),
                m.TimeEntry.deleted_at.is_(None),
                func.lower(m.Tag.name) == "development",
                m.Tag.workspace_id == ws_id,
            )
        )
    ).all()
    hit: dict[str, set[int]] = defaultdict(set)
    for pid, day in rows:
        hit[pid].add(min((day - _d(start)).days // 7, weeks - 1))
    members = (
        await s.execute(
            select(m.WorkspaceMember.person_id).where(
                m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.deleted_at.is_(None)
            )
        )
    ).scalars()
    return {pid: Counts(len(hit.get(pid, ())), weeks) for pid in members}


def detectors() -> dict[str, Detector]:
    """Registry of detector functions keyed by habit definition key."""
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
        "learning_consistency": d_learning_consistency,
    }
