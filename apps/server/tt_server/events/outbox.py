"""Transactional outbox: defer Procrastinate jobs inside the same DB transaction as the Event rows."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.capture import Event

from tt_server.jobs.otel import inject_context
from tt_server.jobs.registry import tasks_for

_DEFER = text(
    """
    SELECT procrastinate_defer_jobs_v1(ARRAY[ROW(
        'default', :task, 0, NULL, NULL, CAST(:args AS jsonb), NULL
    )::procrastinate_job_to_defer_v1])
    """
)


async def dispatch(session: AsyncSession, events: list[Event]) -> int:
    """Enqueue follow-up jobs for `events`; returns how many jobs were deferred."""
    deferred = 0
    for event in events:
        for task in tasks_for(event.event_type):
            args = json.dumps({"event_id": event.id, **inject_context()})
            await session.execute(_DEFER, {"task": task, "args": args})
            deferred += 1
    return deferred
