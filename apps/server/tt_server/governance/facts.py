"""Preloaded facts behind the Tier-3 CEL functions.

CEL evaluation is synchronous and must never touch the database, so every function reads a dictionary filled here, once per batch, by
grouped queries (one statement per fact group, never one per item). The engine asks for exactly the groups a policy's functions need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import ActivityStatus, StateCategory, TargetType, ViolationStatus

from tt_server.db import models as m

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
OPEN = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


def window_start(window: str, now: datetime, tz: str = "UTC") -> datetime:
    """`today` = since local midnight (UTC here: policy evaluation is workspace-wide), `7d`/`30d`/`12h` = a sliding window."""
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    n, unit = int(window[:-1]), window[-1]
    return now - (
        timedelta(days=n) if unit == "d" else timedelta(hours=n) if unit == "h" else timedelta(weeks=n)
    )


@dataclass
class Facts:
    now: datetime
    evidence_last: dict[str, datetime] = field(default_factory=dict)
    evidence_count: dict[tuple[str, str | None], int] = field(default_factory=dict)
    hours: dict[tuple[str, str], float] = field(default_factory=dict)
    reopens: dict[str, int] = field(default_factory=dict)
    children: dict[str, int] = field(default_factory=dict)
    violations: dict[tuple[str, str | None], int] = field(default_factory=dict)
    stage_days: dict[str, int] = field(default_factory=dict)
    missing_dims: dict[str, list[str]] = field(default_factory=dict)
    wip: dict[str, int] = field(default_factory=dict)  # "<project key>|<stage key>" and "<project key>|"
    minutes: dict[tuple[str, str], int] = field(default_factory=dict)
    distinct: dict[tuple[str, str], int] = field(default_factory=dict)
    development: dict[tuple[str, str], int] = field(default_factory=dict)  # minutes on `#development` items
    active: dict[str, int] = field(default_factory=dict)
    overrun: dict[tuple[str, str], float] = field(default_factory=dict)


async def load(
    s: AsyncSession, ws_id: str, needs: set[str], *, now: datetime, item_ids: list[str], person_ids: list[str],
    item_rows: dict[str, dict[str, Any]], windows: set[str], project_keys: list[str],
) -> Facts:  # fmt: skip
    """Fill the fact groups in `needs` for this batch."""
    f = Facts(now=now)
    if item_ids and "evidence" in needs:
        for iid, last, n in (
            await s.execute(
                select(m.Evidence.work_item_id, func.max(m.Evidence.captured_at), func.count())
                .where(m.Evidence.work_item_id.in_(item_ids), m.Evidence.deleted_at.is_(None))
                .group_by(m.Evidence.work_item_id)
            )
        ).all():
            f.evidence_last[iid], f.evidence_count[(iid, None)] = last, n
        for iid, kind, n in (
            await s.execute(
                select(m.Evidence.work_item_id, m.Evidence.kind, func.count())
                .where(m.Evidence.work_item_id.in_(item_ids), m.Evidence.deleted_at.is_(None))
                .group_by(m.Evidence.work_item_id, m.Evidence.kind)
            )
        ).all():
            f.evidence_count[(iid, kind.value)] = n
    if item_ids and "hours" in needs:
        for w in windows or {"today", "7d"}:
            since = window_start(w, now).date()
            for iid, mins in (
                await s.execute(
                    select(m.TimeEntry.work_item_id, func.sum(m.TimeEntry.minutes))
                    .where(
                        m.TimeEntry.work_item_id.in_(item_ids),
                        m.TimeEntry.date >= since,
                        m.TimeEntry.deleted_at.is_(None),
                    )
                    .group_by(m.TimeEntry.work_item_id)
                )
            ).all():
                f.hours[(iid, w)] = float(mins or 0) / 60.0
    if item_ids and "reopens" in needs:
        for tid, n in (
            await s.execute(
                select(m.Event.entity_id, func.count())
                .where(
                    m.Event.entity_type == TargetType.WORK_ITEM,
                    m.Event.entity_id.in_(item_ids),
                    m.Event.event_type == "item.reopened",
                )
                .group_by(m.Event.entity_id)
            )
        ).all():
            f.reopens[tid] = n
    if item_ids and "children" in needs:
        for pid, n in (
            await s.execute(
                select(m.WorkItem.parent_id, func.count())
                .where(m.WorkItem.parent_id.in_(item_ids), m.WorkItem.deleted_at.is_(None))
                .group_by(m.WorkItem.parent_id)
            )
        ).all():
            if pid is not None:
                f.children[pid] = n
    if item_ids and "violations" in needs:
        for tid, cat, n in (
            await s.execute(
                select(m.Violation.target_id, m.Violation.category, func.count())
                .where(
                    m.Violation.target_type == TargetType.WORK_ITEM,
                    m.Violation.target_id.in_(item_ids),
                    m.Violation.status.in_(OPEN),
                )
                .group_by(m.Violation.target_id, m.Violation.category)
            )
        ).all():
            f.violations[(tid, cat.value)] = n
            f.violations[(tid, None)] = f.violations.get((tid, None), 0) + n
    for iid, row in item_rows.items():
        entered = (row.get("placement") or {}).get("entered_at")
        if entered is not None:
            f.stage_days[iid] = (now - entered).days
    if item_ids and "dimensions" in needs:
        f.missing_dims = await _missing_dimensions(s, ws_id, item_rows)
    if "wip" in needs:
        stmt = (
            select(m.Project.key, m.Stage.key, func.count())
            .select_from(m.StagePlacement)
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .join(m.WorkItem, m.WorkItem.id == m.StagePlacement.target_id)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(
                m.StagePlacement.target_type == TargetType.WORK_ITEM, m.StagePlacement.exited_at.is_(None),
                m.Project.workspace_id == ws_id, m.WorkItem.deleted_at.is_(None), m.WorkflowState.category != StateCategory.DONE,
            )
            .group_by(m.Project.key, m.Stage.key)
        )  # fmt: skip
        for pk, sk, n in (await s.execute(stmt)).all():
            f.wip[f"{pk}|{sk}"] = n
            f.wip[f"{pk}|"] = f.wip.get(f"{pk}|", 0) + n
    if person_ids and ("minutes" in needs):
        for w in windows or {"today"}:
            since = window_start(w, now).date()
            for pid, mins, n in (
                await s.execute(
                    select(
                        m.TimeEntry.person_id,
                        func.sum(m.TimeEntry.minutes),
                        func.count(func.distinct(m.TimeEntry.work_item_id)),
                    )
                    .where(
                        m.TimeEntry.person_id.in_(person_ids),
                        m.TimeEntry.date >= since,
                        m.TimeEntry.deleted_at.is_(None),
                    )
                    .group_by(m.TimeEntry.person_id)
                )
            ).all():
                f.minutes[(pid, w)], f.distinct[(pid, w)] = int(mins or 0), n
    if person_ids and "development" in needs:
        for w in windows or {"7d"}:
            since = window_start(w, now).date()
            dev_rows = await s.execute(
                select(m.TimeEntry.person_id, func.sum(m.TimeEntry.minutes))
                .join(m.TagLink, (m.TagLink.target_id == m.TimeEntry.work_item_id) & (m.TagLink.target_type == TargetType.WORK_ITEM))
                .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
                .where(
                    m.TimeEntry.person_id.in_(person_ids),
                    m.TimeEntry.date >= since,
                    m.TimeEntry.deleted_at.is_(None),
                    func.lower(m.Tag.name) == "development",
                    m.Tag.workspace_id == ws_id,
                )
                .group_by(m.TimeEntry.person_id)
            )  # fmt: skip
            for pid, mins in dev_rows.all():
                f.development[(pid, w)] = int(mins or 0)
    if person_ids and "active" in needs:
        for pid, n in (
            await s.execute(
                select(m.Activity.person_id, func.count(func.distinct(m.Activity.work_item_id)))
                .where(m.Activity.person_id.in_(person_ids), m.Activity.status == ActivityStatus.ACTIVE)
                .group_by(m.Activity.person_id)
            )
        ).all():
            f.active[pid] = n
    if project_keys and "overrun" in needs:
        for w in windows or {"30d"}:
            since = window_start(w, now).date()
            rows = (
                await s.execute(
                    select(
                        m.Project.key,
                        func.count(),
                        func.count().filter(m.WorkItem.actual_end > m.WorkItem.planned_end),
                    )
                    .select_from(m.WorkItem)
                    .join(m.Project, m.Project.id == m.WorkItem.project_id)
                    .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
                    .where(
                        m.Project.workspace_id == ws_id,
                        m.WorkflowState.category == StateCategory.DONE,
                        m.WorkItem.actual_end >= since,
                    )
                    .group_by(m.Project.key)
                )
            ).all()
            for pk, total, late in rows:
                f.overrun[(pk, w)] = (late / total) if total else 0.0
    return f


async def _missing_dimensions(
    s: AsyncSession, ws_id: str, item_rows: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    dims = (
        await s.scalars(
            select(m.Dimension).where(
                m.Dimension.workspace_id == ws_id,
                m.Dimension.is_required_for.is_not(None),
                m.Dimension.deleted_at.is_(None),
            )
        )
    ).all()
    out: dict[str, list[str]] = {}
    for iid, row in item_rows.items():
        for d in dims:
            when = d.is_required_for or {}
            stages, cats = when.get("stages"), when.get("state_category")
            if stages and (row["stage"]["key"] not in stages):
                continue
            if cats and row["state"]["category"] not in cats:
                continue
            if not cats and not stages and row["state"]["category"] == "done":
                continue
            if row["classification"].get(d.key) in (None, []):
                out.setdefault(iid, []).append(d.key)
    return out


def date_ts(d: date | datetime | None) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def day_ts(d: date | datetime | None) -> datetime | None:
    """Day-granular timestamp (UTC midnight): `actual_end > planned_end` means "a later day", as the plan dates are days."""
    if d is None:
        return None
    return date_ts(d.astimezone(UTC).date() if isinstance(d, datetime) else d)
