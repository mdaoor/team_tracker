"""Procrastinate task of the Jira projection (Phase 10): one push per event, retried with backoff while Jira is down."""

from __future__ import annotations

from procrastinate import RetryStrategy

from tt_server.db import models as m
from tt_server.db.session import make_session_factory
from tt_server.integrations.jira import sync
from tt_server.jobs.engine import shared_engine
from tt_server.jobs.otel import traced_task
from tt_server.settings import get_settings


async def item_of_event(session, event_id: str) -> str | None:  # type: ignore[no-untyped-def]
    ev = await session.get(m.Event, event_id)
    if ev is None or ev.source.value == "import" and ev.event_type == "item.jira_linked":
        return None  # our own link event: nothing to push
    if ev.entity_type.value == "work_item":
        return str(ev.entity_id)
    return None


async def handle_event(factory, event_id: str) -> str:  # type: ignore[no-untyped-def]
    """Project one event to Jira: a merged-away item announces the merge on its issue, any other item event pushes the item."""
    async with factory() as s:
        ev = await s.get(m.Event, event_id)
        merged_into = None
        if ev is not None and ev.event_type == "item.merged":
            merged_into = ((ev.payload or {}).get("after") or {}).get("merged_into")
        entity_id = str(ev.entity_id) if ev is not None else None
        item_id = await item_of_event(s, event_id)
    if merged_into and entity_id:
        return (await sync.push_merge(factory, entity_id, str(merged_into))).status
    if item_id is None:
        return "not_an_item_event"
    return (await sync.push_item(factory, item_id)).status


@traced_task(name="jira.on_event", retry=RetryStrategy(max_attempts=sync.MAX_ATTEMPTS, exponential_wait=15))
async def on_event(event_id: str) -> str:
    if not get_settings().features.jira_sync:
        return "disabled"
    return await handle_event(make_session_factory(shared_engine()), event_id)
