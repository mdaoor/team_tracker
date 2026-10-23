"""`tt-admin` operations CLI."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import typer
from sqlalchemy import text

from tt_server.db.base import Base
from tt_server.db.session import make_engine, make_session_factory

app = typer.Typer(help="Team Tracker administration", no_args_is_help=True)
seed_app = typer.Typer(help="Seed data", no_args_is_help=True)
app.add_typer(seed_app, name="seed")


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "tools" / "seed" / "demo.py").is_file():
            return parent
    raise typer.BadParameter("tools/seed/demo.py not found; run from a repository checkout")


def _load_demo() -> ModuleType:
    root = _repo_root()
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("tt_seed_demo", root / "tools" / "seed" / "demo.py")
    if spec is None or spec.loader is None:
        raise typer.BadParameter("cannot load tools/seed/demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed(reset: bool) -> str:
    demo = _load_demo()
    engine = make_engine()
    try:
        async with engine.begin() as conn:
            exists = await conn.scalar(text("SELECT count(*) FROM workspaces WHERE slug = 'acme'"))
            if exists and not reset:
                return "workspace 'acme' already exists (use --reset to recreate)"
            if reset:
                tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
                await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        count = await demo.persist_demo(make_session_factory(engine))
        return f"seeded {count} work items into workspace 'acme'"
    finally:
        await engine.dispose()


@app.command("migrate")
def migrate() -> None:
    """Apply Alembic migrations and the Procrastinate queue schema."""
    from alembic import command
    from alembic.config import Config

    from tt_server.jobs.schema import ensure_procrastinate_schema
    from tt_server.settings import get_settings

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")
    applied = ensure_procrastinate_schema(get_settings().psycopg_dsn)
    typer.echo("migrations applied" + ("; procrastinate schema applied" if applied else ""))


@app.command("backfill-overlay")
def backfill_overlay() -> None:
    """One-off: give pre-Phase-3 workspaces dimensions, default process, weight scheme and mirrors."""
    asyncio.run(_backfill())


async def _backfill() -> None:
    from sqlalchemy import select as sa_select

    from tt_server.db import models as m
    from tt_server.db.uow import UnitOfWork
    from tt_server.domain.overlay import weights
    from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, sync_item_mirrors
    from tt_server.domain.overlay.process import ensure_default_process

    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        async with UnitOfWork(factory) as uow:
            for ws in (await uow.session.scalars(sa_select(m.Workspace))).all():
                await ensure_builtin_dimensions(uow.session, ws.id)
                await ensure_default_process(uow.session, ws.id)
                await weights.ensure_default_scheme(uow.session, ws.id)
                rows = (
                    await uow.session.scalars(
                        sa_select(m.WorkItem)
                        .join(m.Project, m.Project.id == m.WorkItem.project_id)
                        .where(m.Project.workspace_id == ws.id)
                    )
                ).all()
                for item in rows:
                    await sync_item_mirrors(uow, ws, item, set_by=item.reporter_id)
                    await weights.recompute_item(uow, ws, item, actor_id=item.reporter_id)
                typer.echo(f"{ws.slug}: {len(rows)} items mirrored")
            await uow.commit(
                allow_unlogged=True
            )  # derived rows only (mirrors / weights): no domain event applies
    finally:
        await engine.dispose()


@seed_app.command("demo")
def seed_demo(reset: bool = typer.Option(False, "--reset", help="Truncate all tables first")) -> None:
    """Create the `acme` demo workspace from tools/gen_snapshot.py (deterministic ids)."""
    typer.echo(asyncio.run(_seed(reset)))


if __name__ == "__main__":
    app()
