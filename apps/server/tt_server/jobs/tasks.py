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


@app.periodic(cron="30 2 * * *")
@app.task(name="idp.sync_directory", queueing_lock="idp.sync_directory")
async def sync_directory(timestamp: int) -> int:
    """Nightly: refresh AD group memberships; people who left a group lose what it granted."""
    from tt_server.db.uow import UnitOfWork
    from tt_server.integrations import idp
    from tt_server.integrations.ldap import ldap_provider_from_settings
    from tt_server.settings import get_settings

    provider = ldap_provider_from_settings(get_settings())
    if provider is None:
        return 0
    engine = make_engine()
    factory = make_session_factory(engine)
    changed = 0
    try:
        async with factory() as s:
            workspaces = (await s.scalars(select(m.Workspace).where(m.Workspace.deleted_at.is_(None)))).all()
        for ws in workspaces:
            async with UnitOfWork(factory) as uow:
                fresh = await uow.session.get(m.Workspace, ws.id)
                assert fresh is not None  # noqa: S101
                changed += (await idp.sync_workspace(uow, fresh, provider.lookup_email)).changed
    finally:
        await engine.dispose()
    return changed


@app.periodic(cron="*/15 * * * *")
@app.task(name="snapshot.refresh", queueing_lock="snapshot.refresh")
async def refresh_snapshots(timestamp: int) -> int:
    """Every 15 min: keep cached snapshots of saved presentations and recently opened scopes fresh (Phase 7 §7.9)."""
    from tt_server.domain import deckdata

    engine = make_engine()
    factory = make_session_factory(engine)
    n = 0
    try:
        async with factory() as s:
            ids = (await s.scalars(select(m.Workspace.id).where(m.Workspace.deleted_at.is_(None)))).all()
        for ws_id in ids:
            n += await deckdata.refresh_cache(factory, ws_id)
    finally:
        await engine.dispose()
    return n


@app.periodic(cron="*/15 * * * *")
@app.task(name="recurrence.materialise", queueing_lock="recurrence.materialise")
async def materialise_recurrences(timestamp: int) -> int:
    """Every 15 min: create the items of recurrence rules whose next occurrence (minus lead days) has arrived."""
    from tt_server.domain import recurrence

    engine = make_engine()
    try:
        return await recurrence.run_due(
            make_session_factory(engine), datetime.fromtimestamp(timestamp, tz=UTC)
        )
    finally:
        await engine.dispose()


@app.periodic(cron="20 3 * * *")
@app.task(name="plan.recompute_progress", queueing_lock="plan.recompute_progress")
async def recompute_progress(timestamp: int) -> int:
    """Nightly safety net: re-derive `progress_pct` of every rolled-up container (the write path keeps it current; this heals drift)."""
    from tt_server.domain.plan import linkage

    engine = make_engine()
    try:
        return await linkage.recompute_all(make_session_factory(engine))
    finally:
        await engine.dispose()


@app.periodic(cron="40 3 * * *")
@app.task(name="retention.purge", queueing_lock="retention.purge")
async def purge_retention(timestamp: int) -> int:
    """Nightly: drop `raw_input` older than each workspace's `raw_input_retention_days` (unset = keep)."""
    from tt_server.domain import privacy

    engine = make_engine()
    try:
        return await privacy.purge_all(make_session_factory(engine))
    finally:
        await engine.dispose()
