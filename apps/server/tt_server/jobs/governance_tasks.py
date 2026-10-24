"""Procrastinate tasks of the governance engine (Phase 8): on-event evaluation, scheduled sweeps, nightly habits and metrics."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from tt_server.db import models as m
from tt_server.db.session import make_engine, make_session_factory
from tt_server.governance import runner
from tt_server.jobs.app import app
from tt_server.settings import get_settings


async def _workspaces() -> list[str]:
    engine = make_engine()
    try:
        async with make_session_factory(engine)() as s:
            return list(
                (await s.scalars(select(m.Workspace.id).where(m.Workspace.deleted_at.is_(None)))).all()
            )
    finally:
        await engine.dispose()


@app.task(name="governance.on_event")
async def on_event(event_id: str) -> int:
    if not get_settings().features.governance:
        return 0
    engine = make_engine()
    try:
        return await runner.on_event(make_session_factory(engine), event_id)
    finally:
        await engine.dispose()


@app.periodic(cron="* * * * *")
@app.task(name="governance.run_scheduled", queueing_lock="governance.run_scheduled")
async def run_scheduled(timestamp: int) -> int:
    """Every minute: sweep the scheduled policies whose cron expression matches this minute."""
    if not get_settings().features.governance:
        return 0
    now = datetime.fromtimestamp(timestamp, tz=UTC)
    engine = make_engine()
    try:
        factory = make_session_factory(engine)
        return sum(
            [await runner.run_workspace(factory, ws, now=now, only_due=True) for ws in await _workspaces()]
        )
    finally:
        await engine.dispose()


@app.periodic(cron="10 4 * * *")
@app.task(name="governance.nightly", queueing_lock="governance.nightly")
async def nightly(timestamp: int) -> int:
    """Habits, metric snapshots and key-result updates, after the daily sweeps."""
    if not (get_settings().features.governance or get_settings().features.habits_nightly):
        return 0
    now = datetime.fromtimestamp(timestamp, tz=UTC)
    engine = make_engine()
    n = 0
    try:
        factory = make_session_factory(engine)
        for ws in await _workspaces():
            n += (await runner.nightly(factory, ws, now=now))["habits"]
    finally:
        await engine.dispose()
    return n
