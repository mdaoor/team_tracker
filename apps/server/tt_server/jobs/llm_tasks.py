"""Procrastinate tasks that call the LLM gateway (Phase 2b §2b.5); no gateway configured = no-ops."""

from __future__ import annotations

from sqlalchemy import select

from tt_server.db import models as m
from tt_server.db.session import make_engine, make_session_factory
from tt_server.domain.overlay import suggestions
from tt_server.integrations.llm import gateway_from_settings
from tt_server.jobs.app import app
from tt_server.settings import get_settings


@app.periodic(cron="15 3 * * *")
@app.task(name="llm.nightly", queueing_lock="llm.nightly")
async def nightly(timestamp: int) -> dict[str, int]:
    """Review-row suggestions and classification pre-fill for every workspace."""
    client = gateway_from_settings(get_settings())
    if client is None:
        return {"written": 0}
    engine = make_engine()
    factory = make_session_factory(engine)
    written = 0
    try:
        async with factory() as s:
            ids = (await s.scalars(select(m.Workspace.id).where(m.Workspace.deleted_at.is_(None)))).all()
        for ws_id in ids:
            written += (await suggestions.run_review_suggestions(factory, client, ws_id)).written
            written += (await suggestions.prefill_classifications(factory, client, ws_id)).written
    finally:
        await client.aclose()
        await engine.dispose()
    return {"written": written}


@app.task(name="llm.suggest_for_event")
async def suggest_for_event(event_id: str) -> str:
    """After `review.created`: suggest right away so the row arrives in the queue with a default."""
    client = gateway_from_settings(get_settings())
    if client is None:
        return "disabled"
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        async with factory() as s:
            ev = await s.get(m.Event, event_id)
            rid = ((ev.payload or {}).get("after") or {}).get("review_item_id") if ev else None
            slug = (
                await s.scalar(select(m.Workspace.slug).where(m.Workspace.id == ev.workspace_id))
                if ev
                else None
            )
        if not (ev and rid and slug):
            return "skipped"
        return await suggestions.suggest_one(factory, client, ev.workspace_id, slug, rid)
    finally:
        await client.aclose()
        await engine.dispose()


@app.periodic(cron="45 3 * * *")
@app.task(name="insights.nightly", queueing_lock="insights.nightly")
async def nightly_insights(timestamp: int) -> dict[str, int]:
    """Grounded project summaries through the gateway (Phase 7 §7.3); no gateway configured = no-op."""
    from tt_server.domain import insights

    client = gateway_from_settings(get_settings())
    if client is None:
        return {"written": 0}
    engine = make_engine()
    factory = make_session_factory(engine)
    written = 0
    try:
        async with factory() as s:
            ids = (await s.scalars(select(m.Workspace.id).where(m.Workspace.deleted_at.is_(None)))).all()
        for ws_id in ids:
            written += await insights.nightly(factory, client, ws_id)
    finally:
        await client.aclose()
        await engine.dispose()
    return {"written": written}
