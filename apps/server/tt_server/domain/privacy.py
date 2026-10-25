"""Data retention and the personal-data rights (phase 12 §4): purge raw input, export one person's data, erase a person.

Rules that outrank convenience:
* An admin's export of someone else never contains wellbeing habits or Inbox items (ADR-0024, §13.3); only the person can export those.
* Erasure anonymises, it does not delete history the organisation needs: events stay (they carry ids, not names) but their `raw_input`
  is nulled, comments and daily-log text are blanked, tokens are revoked and the member is suspended."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import utcnow
from tt_schema.enums import MemberStatus, PolicyCategory, ProjectKind, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork, commit_derived
from tt_server.domain.common import emit

ERASED = "[erased]"


async def purge_raw_input(session: AsyncSession, *, days: int, workspace_id: str | None = None) -> int:
    """Null `Event.raw_input` older than `days` (the structured event stays). Returns rows changed."""
    cutoff = utcnow() - timedelta(days=days)
    stmt = (
        update(m.Event)
        .where(m.Event.raw_input.is_not(None), m.Event.received_at < cutoff)
        .values(raw_input=None)
    )
    if workspace_id:
        stmt = stmt.where(m.Event.workspace_id == workspace_id)
    res = await session.execute(stmt)
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def purge_all(factory: Any) -> int:
    """Nightly: apply each workspace's `raw_input_retention_days` (unset = keep forever)."""
    total = 0
    async with factory() as s:
        for ws in (await s.scalars(select(m.Workspace).where(m.Workspace.deleted_at.is_(None)))).all():
            days = (ws.settings or {}).get("raw_input_retention_days")
            if isinstance(days, int) and days > 0:
                total += await purge_raw_input(s, days=days, workspace_id=ws.id)
        await commit_derived(s)
    return total


async def export_person(
    session: AsyncSession, ws: m.Workspace, person_id: str, *, include_private: bool
) -> dict[str, Any]:
    person = await session.get(m.Person, person_id)
    member = await session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.person_id == person_id
        )
    )
    if person is None or member is None:
        raise LookupError("no such member")
    projects = {
        p.id: p
        for p in (await session.scalars(select(m.Project).where(m.Project.workspace_id == ws.id))).all()
    }

    def visible_project(pid: str) -> bool:
        p = projects.get(pid)
        return p is not None and (include_private or p.kind != ProjectKind.INBOX)

    items = (
        await session.scalars(
            select(m.WorkItem).where(
                sa.or_(m.WorkItem.reporter_id == person_id, m.WorkItem.assignee_id == person_id),
                m.WorkItem.deleted_at.is_(None),
            )
        )
    ).all()
    item_by_id = {i.id: i for i in items}
    times = (await session.scalars(select(m.TimeEntry).where(m.TimeEntry.person_id == person_id))).all()
    comments = (
        await session.scalars(
            select(m.Comment).where(m.Comment.author_id == person_id, m.Comment.deleted_at.is_(None))
        )
    ).all()
    logs = (await session.scalars(select(m.DailyLog).where(m.DailyLog.person_id == person_id))).all()
    events = (
        await session.scalars(
            select(m.Event)
            .where(m.Event.workspace_id == ws.id, m.Event.actor_id == person_id)
            .order_by(m.Event.occurred_at.desc())
            .limit(2000)
        )
    ).all()
    out: dict[str, Any] = {
        "exported_at": utcnow().isoformat(),
        "scope": "full (self-service)"
        if include_private
        else "administrative (excludes Inbox and wellbeing data)",
        "person": {
            "id": person.id,
            "display_name": person.display_name,
            "email": person.email,
            "time_zone": person.time_zone,
        },
        "membership": {"handle": member.handle, "status": member.status.value, "workspace": ws.slug},
        "items": [
            {
                "key": i.key,
                "title": i.title,
                "role": "reporter" if i.reporter_id == person_id else "assignee",
                "created_at": i.created_at.isoformat(),
            }
            for i in items
            if visible_project(i.project_id)
        ],
        "time_entries": [
            {
                "date": t.date.isoformat(),
                "minutes": t.minutes,
                "item": item_by_id[t.work_item_id].key if t.work_item_id in item_by_id else None,
                "note": t.note,
            }
            for t in times
            if include_private
            or t.work_item_id not in item_by_id
            or visible_project(item_by_id[t.work_item_id].project_id)
        ],
        "comments": [{"at": c.created_at.isoformat(), "body": c.body} for c in comments],
        "daily_logs": [
            {
                "date": d.date.isoformat(),
                "summary": d.summary,
                "blockers": d.blockers,
                "status": d.status.value,
            }
            for d in logs
        ],
        "events": [
            {
                "at": e.occurred_at.isoformat(),
                "type": e.event_type,
                "entity_type": e.entity_type.value,
                "entity_id": e.entity_id,
                "raw_input": e.raw_input,
            }
            for e in events
            if include_private or e.entity_type != TargetType.PERSON or e.entity_id == person_id
        ],
    }
    habits = (
        await session.scalars(
            select(m.Habit).where(
                m.Habit.workspace_id == ws.id,
                m.Habit.subject_type == TargetType.PERSON,
                m.Habit.subject_id == person_id,
            )
        )
    ).all()
    defs = {d.key: d.category for d in (await session.scalars(select(m.HabitDefinition))).all()}
    out["habits"] = [
        {
            "key": h.key,
            "window": h.window,
            "occurrences": h.occurrences,
            "rate": float(h.rate),
            "status": h.status.value,
        }
        for h in habits
        if include_private or defs.get(h.key) != PolicyCategory.WELLBEING
    ]
    return out


async def erase_person(uow: UnitOfWork, ws: m.Workspace, person_id: str, *, actor_id: str) -> dict[str, int]:
    s = uow.session
    person = await s.get(m.Person, person_id)
    member = await s.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.person_id == person_id
        )
    )
    if person is None or member is None:
        raise LookupError("no such member")
    counts = {"comments": 0, "daily_logs": 0, "tokens": 0, "events": 0}
    tag = person.id[-8:]
    person.display_name, person.email, person.is_active = "Erased user", f"erased+{tag}@invalid", False
    person.deleted_at = utcnow()
    member.handle, member.status = f"@erased-{tag}", MemberStatus.SUSPENDED
    for c in (await s.scalars(select(m.Comment).where(m.Comment.author_id == person_id))).all():
        if c.body != ERASED:
            c.body = ERASED
            counts["comments"] += 1
    for d in (await s.scalars(select(m.DailyLog).where(m.DailyLog.person_id == person_id))).all():
        d.summary, d.blockers = "", None
        counts["daily_logs"] += 1
    for t in (
        await s.scalars(
            select(m.ApiToken).where(m.ApiToken.person_id == person_id, m.ApiToken.deleted_at.is_(None))
        )
    ).all():
        t.deleted_at = utcnow()
        counts["tokens"] += 1
    res = await s.execute(
        update(m.Event)
        .where(m.Event.actor_id == person_id, m.Event.raw_input.is_not(None))
        .values(raw_input=None)
    )
    counts["events"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    emit(
        uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.PERSON, entity_id=person_id,
        event_type="person.erased", payload={"after": counts},
    )  # fmt: skip
    return counts
