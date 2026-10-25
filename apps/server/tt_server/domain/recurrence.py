"""Recurring work (plan P1 RecurrenceRule): a template item + an RFC 5545 rule; a job materialises one item per occurrence.

Idempotent by construction: an occurrence is identified by `source.recurrence = {rule, occurrence}` on the item it created, so a
retried or doubly-scheduled run cannot create it twice."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrule, rrulestr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import utcnow
from tt_schema.enums import EventSource, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid
from tt_server.domain.items import service as items

MAX_CATCH_UP = (
    10  # occurrences created per rule per run after downtime; older ones are skipped, not back-filled in bulk
)


def parse(rule: str, tz: str, start: datetime) -> rrule:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Invalid("invalid_rule", f"unknown time zone {tz!r}", pointer="/time_zone") from exc
    try:
        r = rrulestr(rule, dtstart=start.astimezone(zone))
    except (ValueError, TypeError) as exc:
        raise Invalid("invalid_rule", f"not a valid RRULE: {exc}", pointer="/rrule") from exc
    if not isinstance(r, rrule):
        raise Invalid("invalid_rule", "use a single RRULE (no RDATE / EXDATE sets)", pointer="/rrule")
    if not re.search(
        r"FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)\b", rule.upper()
    ):  # hourly or finer would flood a project
        raise Invalid("invalid_rule", "the finest supported frequency is DAILY", pointer="/rrule")
    return r


def occurrences_after(rule: str, tz: str, start: datetime, after: datetime, n: int) -> list[datetime]:
    r = parse(rule, tz, start)
    out: list[datetime] = []
    cur = after.astimezone(ZoneInfo(tz))
    for _ in range(n):
        nxt = r.after(cur, inc=False)
        if nxt is None:
            break
        out.append(nxt.astimezone(UTC))
        cur = nxt
    return out


def first_run(rule: str, tz: str, start: datetime) -> datetime | None:
    got = occurrences_after(rule, tz, start, start - timedelta(seconds=1), 1)
    return got[0] if got else None


async def materialise_rule(uow: UnitOfWork, ws_id: str, rule: m.RecurrenceRule, now: datetime) -> int:
    """Create due occurrences for one rule. Returns how many items were created."""

    s = uow.session
    tpl = await s.get(m.WorkItem, rule.template_item_id)
    project = await s.get(m.Project, rule.project_id)
    ws = await s.get(m.Workspace, ws_id)
    if tpl is None or project is None or ws is None or tpl.deleted_at is not None:
        rule.is_active = False  # the template is gone: stop quietly rather than fail every 15 minutes
        return 0
    created = 0
    uow.context["actor_id"] = tpl.reporter_id
    horizon = now + timedelta(days=rule.lead_days)
    guard = 0
    while rule.next_run_at is not None and rule.next_run_at <= horizon and guard < MAX_CATCH_UP * 3:
        guard += 1
        occ = rule.next_run_at
        marker: dict[str, object] = {"recurrence": {"rule": rule.id, "occurrence": occ.isoformat()}}
        exists = await s.scalar(
            select(m.WorkItem.id).where(
                m.WorkItem.project_id == project.id,
                m.WorkItem.source.contains(marker),
            )
        )
        stale = occ < now - timedelta(days=1) and created >= MAX_CATCH_UP
        if not exists and not stale:
            local = occ.astimezone(ZoneInfo(rule.time_zone)).date()
            assignee = await _assignee(s, ws_id, rule, tpl)
            item = await items.create_item(
                uow, ws, project, title=tpl.title, reporter_id=tpl.reporter_id, assignee_id=assignee, type_id=tpl.type_id,
                parent=await s.get(m.WorkItem, tpl.parent_id) if tpl.parent_id else None, description=tpl.description,
                priority=tpl.priority, source=marker, planned_start=local, planned_end=local,
            )  # fmt: skip
            emit(
                uow, workspace_id=ws_id, actor_id=tpl.reporter_id, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
                event_type="item.created", payload={"after": {"key": item.key, "title": item.title, "recurrence_rule": rule.id, "due": local.isoformat()}},
                source=EventSource.RULE,
            )  # fmt: skip
            created += 1
        nxt = occurrences_after(rule.rrule, rule.time_zone, rule.created_at, occ, 1)
        rule.next_run_at = nxt[0] if nxt else None
        if rule.next_run_at is None:
            rule.is_active = False  # a finite rule (COUNT/UNTIL) has run out
    return created


async def _assignee(s: AsyncSession, ws_id: str, rule: m.RecurrenceRule, tpl: m.WorkItem) -> str | None:
    handle = (rule.assign_to or {}).get("handle")
    if handle:
        person_id = await s.scalar(
            select(m.WorkspaceMember.person_id).where(
                m.WorkspaceMember.workspace_id == ws_id,
                m.WorkspaceMember.handle == handle,
                m.WorkspaceMember.deleted_at.is_(None),
            )
        )
        if person_id:
            return str(person_id)
    return tpl.assignee_id


async def run_due(factory: Any, now: datetime | None = None) -> int:
    now = now or utcnow()
    async with factory() as s:
        ids = (
            await s.execute(
                select(m.RecurrenceRule.id, m.Project.workspace_id, m.RecurrenceRule.lead_days, m.RecurrenceRule.next_run_at)
                .join(m.Project, m.Project.id == m.RecurrenceRule.project_id)
                .where(m.RecurrenceRule.is_active.is_(True), m.RecurrenceRule.deleted_at.is_(None), m.RecurrenceRule.next_run_at.is_not(None))
            )
        ).all()  # fmt: skip
    total = 0
    for rid, ws_id, lead, nxt in ids:
        if nxt is None or nxt - timedelta(days=lead) > now:
            continue
        async with UnitOfWork(factory) as uow:
            rule = await uow.session.get(m.RecurrenceRule, rid)
            if rule is None:
                continue
            n = await materialise_rule(uow, ws_id, rule, now)
            await uow.commit(
                allow_unlogged=True
            )  # advancing next_run_at on a rule that produced nothing is bookkeeping
            total += n
    return total


def due_date(d: date) -> str:
    return d.isoformat()
