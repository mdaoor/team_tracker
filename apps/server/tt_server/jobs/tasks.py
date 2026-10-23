"""Procrastinate tasks (Phase 1): daily-log builder."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from tt_server.db import models as m
from tt_server.db.session import make_engine, make_session_factory
from tt_server.domain.capture import dailylog
from tt_server.domain.common import zone
from tt_server.jobs.app import app


@app.periodic(cron="*/15 * * * *")
@app.task(name="dailylog.build_due", queueing_lock="dailylog.build_due")
async def build_due_daily_logs(timestamp: int) -> int:
    """Every 15 min: draft logs for members whose local time just passed the workspace daily_log_time."""
    engine = make_engine()
    factory = make_session_factory(engine)
    built = 0
    try:
        now = datetime.fromtimestamp(timestamp, tz=UTC)
        async with factory() as s:
            workspaces = (await s.scalars(select(m.Workspace).where(m.Workspace.deleted_at.is_(None)))).all()
            members = [
                (w, mem, await s.get(m.Person, mem.person_id))
                for w in workspaces
                for mem in (
                    await s.scalars(select(m.WorkspaceMember).where(m.WorkspaceMember.workspace_id == w.id))
                ).all()
            ]
        due: dict[tuple[str, date], list[str]] = {}
        for w, mem, person in members:
            local = now.astimezone(zone(person.time_zone))  # type: ignore[union-attr]
            if dailylog.due_now(local, (w.settings or {}).get("daily_log_time", "17:30")):
                due.setdefault((w.id, local.date()), []).append(mem.id)
        for (ws_id, day), _ids in due.items():
            built += await dailylog.build_for_workspace(factory, ws_id, day)
    finally:
        await engine.dispose()
    return built
