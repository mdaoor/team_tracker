"""DailyLog drafts: built from the day's activities, time entries and client events."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id, utcnow
from tt_schema.enums import DailyLogStatus, EventSource, TargetType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person, zone
from tt_server.domain.errors import NotFound


def to_dict(row: m.DailyLog) -> dict[str, Any]:
    """Serialise a daily log row to its API dict (ISO date, status, summary and totals)."""
    return {
        "id": row.id,
        "person_id": row.person_id,
        "date": row.date.isoformat(),
        "summary": row.summary,
        "item_ids": row.item_ids,
        "minutes_total": row.minutes_total,
        "blockers": row.blockers,
        "status": row.status.value,
        "reviewed_by": row.reviewed_by,
    }


async def get_for(uow: UnitOfWork, ws_id: str, log_id: str) -> m.DailyLog:
    """Load a daily log of the workspace or raise NotFound, so other workspaces' logs stay invisible."""
    row = await uow.session.scalar(
        select(m.DailyLog).where(m.DailyLog.id == log_id, m.DailyLog.workspace_id == ws_id)
    )
    if row is None:
        raise NotFound("not_found", "daily log not found")
    return row


async def list_visible(
    uow: UnitOfWork,
    ws: m.Workspace,
    member: m.WorkspaceMember,
    acl: AclContext,
    me: str,
    *,
    person: str | None,
    day: date | None,
) -> tuple[list[m.DailyLog], bool]:
    """The daily logs the viewer may see for `person` (a handle or id; default and `me` are the viewer), newest first.

    Asking for your own day that has work but no draft yet drafts it now (`tt today` must not say "none" after you logged);
    the second value says whether a draft was created, so the caller knows to commit."""
    stmt = visible(select(m.DailyLog).where(m.DailyLog.workspace_id == ws.id), acl, m.DailyLog)
    subject = me
    if person not in (None, "me"):
        subject = (
            await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == ws.id,
                    (m.WorkspaceMember.handle == person) | (m.WorkspaceMember.person_id == person),
                )
            )
            or ""
        )
    stmt = stmt.where(m.DailyLog.person_id == subject)
    drafted = False
    if day:
        stmt = stmt.where(m.DailyLog.date == day)
        if subject == me and not (await uow.session.scalars(stmt.limit(1))).first():
            uow.context["actor_id"] = me
            drafted = bool(await build_for_person(uow, ws, member, day, me))
    rows = (await uow.session.scalars(stmt.order_by(m.DailyLog.date.desc()).limit(100))).all()
    return list(rows), drafted


def _day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = zone(tz_name)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


async def build_for_person(
    uow: UnitOfWork,
    ws: m.Workspace,
    member: m.WorkspaceMember,
    day: date,
    actor_id: str,
    *,
    quiet: bool = False,
) -> m.DailyLog | None:
    """Upsert the draft for `member` on a local `day`; None if no activity. Never edits confirmed logs.

    `quiet=True` (the per-message ingest path) emits no event when the draft is already up to date."""
    session = uow.session
    person = await session.get(m.Person, member.person_id)
    assert person is not None  # noqa: S101
    start, end = _day_bounds(day, person.time_zone)
    acts = (
        await session.scalars(
            select(m.Activity).where(
                m.Activity.person_id == person.id, m.Activity.started_at >= start, m.Activity.started_at < end
            )
        )
    ).all()
    times = (
        await session.scalars(
            select(m.TimeEntry).where(m.TimeEntry.person_id == person.id, m.TimeEntry.date == day)
        )
    ).all()
    client_events = await session.scalar(
        select(func.count())
        .select_from(m.Event)
        .where(
            m.Event.workspace_id == ws.id,
            m.Event.actor_id == person.id,
            m.Event.source == EventSource.CLIENT,
            m.Event.occurred_at >= start,
            m.Event.occurred_at < end,
        )
    )
    if not acts and not times and not client_events:
        return None
    minutes: dict[str, int] = {}
    blockers: list[str] = []
    for a in acts:
        minutes.setdefault(a.work_item_id, 0)
        if a.kind.value == "blocked" and a.note:
            blockers.append(a.note)
    for t in times:
        minutes[t.work_item_id] = minutes.get(t.work_item_id, 0) + t.minutes
    item_rows = (
        (await session.scalars(select(m.WorkItem).where(m.WorkItem.id.in_(list(minutes))))).all()
        if minutes
        else []
    )
    lines = []
    for it in sorted(item_rows, key=lambda r: r.key):
        mins = minutes[it.id]
        lines.append(f"- {it.key} {it.title} — {mins / 60:.1f}h" if mins else f"- {it.key} {it.title}")
    log = await session.scalar(
        select(m.DailyLog).where(
            m.DailyLog.workspace_id == ws.id, m.DailyLog.person_id == person.id, m.DailyLog.date == day
        )
    )
    created = log is None
    if log is None:
        log = m.DailyLog(id=new_id(), workspace_id=ws.id, person_id=person.id, date=day)
        session.add(log)
    elif log.status != DailyLogStatus.DRAFT:
        return log
    summary, item_ids = "\n".join(lines), sorted(minutes)
    total, blocked = sum(minutes.values()), "; ".join(blockers) or None
    if (
        quiet
        and not created
        and (log.summary, log.item_ids, log.minutes_total, log.blockers)
        == (
            summary,
            item_ids,
            total,
            blocked,
        )
    ):
        return log  # nothing changed: no new event
    log.summary, log.item_ids, log.minutes_total, log.blockers = summary, item_ids, total, blocked
    await session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.DAILY_LOG,
        entity_id=log.id,
        event_type="dailylog.drafted",
        payload={"after": {"date": day.isoformat(), "created": created}},
        source=EventSource.RULE,
    )
    return log


async def build_for_workspace(
    session_factory: async_sessionmaker[AsyncSession], ws_id: str, day: date
) -> int:
    """Draft logs for every member with activity on `day` (the person-local day). Returns the number built."""
    built = 0
    async with UnitOfWork(session_factory) as uow:
        ws = await uow.session.get(m.Workspace, ws_id)
        assert ws is not None  # noqa: S101
        bot = await system_person(uow.session)
        members = (
            await uow.session.scalars(
                select(m.WorkspaceMember).where(
                    m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.deleted_at.is_(None)
                )
            )
        ).all()
        for member in members:
            if await build_for_person(uow, ws, member, day, bot.id):
                built += 1
        await uow.commit()
    return built


async def confirm(uow: UnitOfWork, ws: m.Workspace, log: m.DailyLog, actor_id: str) -> None:
    """Mark a draft daily log confirmed by its owner (idempotent) and emit the confirmation event."""
    if log.status == DailyLogStatus.DRAFT:
        log.status = DailyLogStatus.CONFIRMED
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.DAILY_LOG,
        entity_id=log.id,
        event_type="dailylog.confirmed",
        payload={"after": {"status": log.status.value}},
    )


async def review(uow: UnitOfWork, ws: m.Workspace, log: m.DailyLog, reviewer_id: str) -> None:
    """Record a manager's review of a daily log (reviewer and timestamp) and emit the review event."""
    log.status, log.reviewed_by, log.reviewed_at = DailyLogStatus.REVIEWED, reviewer_id, utcnow()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=reviewer_id,
        entity_type=TargetType.DAILY_LOG,
        entity_id=log.id,
        event_type="dailylog.reviewed",
        payload={"after": {"status": "reviewed"}},
    )


def due_now(local_now: datetime, daily_log_time: str, window_minutes: int = 15) -> bool:
    """True when `local_now` falls within [daily_log_time, daily_log_time + window)."""
    hh, mm = (int(x) for x in daily_log_time.split(":"))
    target = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target <= local_now < target + timedelta(minutes=window_minutes)
