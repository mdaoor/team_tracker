"""Procrastinate tasks of the governance engine (Phase 8): on-event evaluation, scheduled sweeps, nightly habits and metrics."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from tt_server.db import models as m
from tt_server.db.session import make_session_factory
from tt_server.governance import runner
from tt_server.jobs.app import app
from tt_server.jobs.engine import shared_engine
from tt_server.jobs.retry import EVENT_RETRY
from tt_server.settings import get_settings


async def _workspaces() -> list[str]:
    engine = shared_engine()
    async with make_session_factory(engine)() as s:
        return list((await s.scalars(select(m.Workspace.id).where(m.Workspace.deleted_at.is_(None)))).all())


@app.task(name="governance.on_event", retry=EVENT_RETRY)
async def on_event(event_id: str) -> int:
    if not get_settings().features.governance:
        return 0
    engine = shared_engine()
    return await runner.on_event(make_session_factory(engine), event_id)


@app.periodic(cron="* * * * *")
@app.task(name="governance.run_scheduled", queueing_lock="governance.run_scheduled")
async def run_scheduled(timestamp: int) -> int:
    """Every minute: sweep the scheduled policies whose cron expression matches this minute."""
    if not get_settings().features.governance:
        return 0
    now = datetime.fromtimestamp(timestamp, tz=UTC)
    engine = shared_engine()
    factory = make_session_factory(engine)
    return sum(
        [await runner.run_workspace(factory, ws, now=now, only_due=True) for ws in await _workspaces()]
    )


@app.periodic(cron="10 4 * * *")
@app.task(name="governance.nightly", queueing_lock="governance.nightly")
async def nightly(timestamp: int) -> int:
    """Habits, metric snapshots and key-result updates, after the daily sweeps."""
    if not (get_settings().features.governance or get_settings().features.habits_nightly):
        return 0
    now = datetime.fromtimestamp(timestamp, tz=UTC)
    engine = shared_engine()
    n = 0
    factory = make_session_factory(engine)
    for ws in await _workspaces():
        n += (await runner.nightly(factory, ws, now=now))["habits"]
    return n
