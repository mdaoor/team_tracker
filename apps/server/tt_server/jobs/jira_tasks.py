"""Procrastinate task of the Jira projection (Phase 10): one push per event, retried with backoff while Jira is down."""

from __future__ import annotations

from procrastinate import RetryStrategy

from tt_server.db import models as m
from tt_server.db.session import make_engine, make_session_factory
from tt_server.integrations.jira import sync
from tt_server.jobs.app import app
from tt_server.settings import get_settings


async def item_of_event(session, event_id: str) -> str | None:  # type: ignore[no-untyped-def]
    ev = await session.get(m.Event, event_id)
    if ev is None or ev.source.value == "import" and ev.event_type == "item.jira_linked":
        return None  # our own link event: nothing to push
    if ev.entity_type.value == "work_item":
        return str(ev.entity_id)
    return None


@app.task(name="jira.on_event", retry=RetryStrategy(max_attempts=sync.MAX_ATTEMPTS, exponential_wait=15))
async def on_event(event_id: str) -> str:
    if not get_settings().features.jira_sync:
        return "disabled"
    engine = make_engine()
    try:
        factory = make_session_factory(engine)
        async with factory() as s:
            item_id = await item_of_event(s, event_id)
        if item_id is None:
            return "not_an_item_event"
        return (await sync.push_item(factory, item_id)).status
    finally:
        await engine.dispose()
