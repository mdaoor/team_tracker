"""Helpers shared by the domain services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.capture import Event
from tt_schema.enums import EventSource, PersonKind, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork

SYSTEM_EMAIL = "system@tt.local"


def emit_admin(
    uow: UnitOfWork,
    *,
    workspace_id: str,
    actor_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: EventSource = EventSource.OVERLAY,
) -> Event:
    """Emit an event whose subject is the acting person: configuration changes with no better entity."""
    return emit(
        uow,
        workspace_id=workspace_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=actor_id,
        event_type=event_type,
        payload=payload,
        source=source,
    )


def emit(
    uow: UnitOfWork,
    *,
    workspace_id: str,
    actor_id: str,
    entity_type: TargetType,
    entity_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: EventSource = EventSource.OVERLAY,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
    client_id: str | None = None,
    raw_input: str | None = None,
) -> Event:
    """Append an Event to the unit of work. `source` must be explicit outside the ingestion path.

    Writes made on behalf of the LLM gateway carry `payload.agent_run_id` (set from `X-Agent-Run-Id`).
    """
    if (run_id := uow.context.get("agent_run_id")) and "agent_run_id" not in (payload or {}):
        payload = {**(payload or {}), "agent_run_id": run_id}
    if (cmd := uow.context.get("command")) and "command" not in (payload or {}):
        # a management command from a client: every event it causes says which op, from which client and mode (Phase 14)
        payload = {**(payload or {}), "command": cmd}
        source = EventSource.CLIENT
    event = Event(
        workspace_id=workspace_id,
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        payload=payload or {},
        source=source,
        occurred_at=occurred_at or utcnow(),
        correlation_id=correlation_id,
        client_id=client_id,
        raw_input=raw_input,
    )
    uow.events.append(event)
    return event


async def system_person(session: AsyncSession) -> m.Person:
    """The bot `Person` used as actor for jobs and other system-originated events."""
    person = await session.scalar(select(m.Person).where(m.Person.email == SYSTEM_EMAIL))
    if person is None:
        person = m.Person(
            id=new_id(), display_name="Team Tracker", email=SYSTEM_EMAIL, kind=PersonKind.BOT, time_zone="UTC"
        )
        session.add(person)
        await session.flush()
    return person


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def aware(dt: datetime) -> datetime:

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def ltree_label(row_id: str) -> str:
    """ltree labels allow only [A-Za-z0-9_]; ids go into `WorkItem.path` with '-' replaced by '_'."""
    return row_id.replace("-", "_")
