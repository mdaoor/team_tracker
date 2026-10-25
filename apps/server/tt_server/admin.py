"""`tt-admin` operations CLI."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

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
        factory = make_session_factory(engine)
        count = await demo.persist_demo(factory)
        await demo.persist_plan_only(factory)  # ERP: a project planned top-down with no reported work
        return f"seeded {count} work items (+ the plan-only project ERP) into workspace 'acme'"
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


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Bind address")] = "0.0.0.0",  # noqa: S104
    port: Annotated[int, typer.Option(help="HTTPS port")] = 8443,
    certfile: Annotated[Path | None, typer.Option(help="PEM certificate (full chain)")] = None,
    keyfile: Annotated[Path | None, typer.Option(help="PEM private key")] = None,
    redirect_port: Annotated[int | None, typer.Option(help="Also redirect plain HTTP from here")] = None,
) -> None:
    """Run the API over HTTPS from a PEM cert/key pair (no cert => plain HTTP, dev only)."""
    import uvicorn

    from tt_server.settings import get_settings

    s = get_settings()
    cert, key = certfile or s.tls_certfile, keyfile or s.tls_keyfile
    if bool(cert) != bool(key):
        raise typer.BadParameter("--certfile and --keyfile must be given together")
    for p in (cert, key):
        if p and not Path(p).is_file():
            raise typer.BadParameter(f"{p}: no such file")
    config = uvicorn.Config(
        "tt_server.main:create_app",
        factory=True,
        host=host,
        port=port,
        ssl_certfile=str(cert) if cert else None,
        ssl_keyfile=str(key) if key else None,
    )
    asyncio.run(_serve(config, host, redirect_port if cert else None, port, s))


@app.command("purge-raw-input")
def purge_raw_input(
    days: Annotated[int, typer.Option(help="Drop raw input older than this many days")],
    workspace: Annotated[str | None, typer.Option(help="Workspace slug (default: all)")] = None,
) -> None:
    """Retention: null `Event.raw_input` older than N days. The structured event stays."""
    from sqlalchemy import select as sa_select

    from tt_server.db import models as m
    from tt_server.domain import privacy

    async def run() -> None:
        engine = make_engine()
        try:
            async with make_session_factory(engine)() as s:
                ws_id = None
                if workspace:
                    ws = await s.scalar(sa_select(m.Workspace).where(m.Workspace.slug == workspace))
                    if ws is None:
                        raise typer.BadParameter(f"unknown workspace {workspace!r}")
                    ws_id = ws.id
                n = await privacy.purge_raw_input(s, days=days, workspace_id=ws_id)
                await s.commit()
                typer.echo(f"{n} event(s) had their raw input removed")
        finally:
            await engine.dispose()

    asyncio.run(run())


@app.command("verify-projection")
def verify_projection(
    workspace: Annotated[str, typer.Argument(help="Workspace slug")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check that every projected item / time entry is explained by an event; exit 1 on drift (see ADR-0040)."""
    import json as _json

    from sqlalchemy import select as sa_select

    from tt_server.db import models as m
    from tt_server.domain import verify

    async def run() -> int:
        engine = make_engine()
        try:
            async with make_session_factory(engine)() as s:
                ws = await s.scalar(sa_select(m.Workspace).where(m.Workspace.slug == workspace))
                if ws is None:
                    raise typer.BadParameter(f"unknown workspace {workspace!r}")
                rep = await verify.verify(s, ws.id)
            if as_json:
                typer.echo(_json.dumps(rep.as_dict(), indent=2))
            elif rep.clean:
                typer.echo("projection and event log agree")
            else:
                for name, rows in rep.findings.items():
                    typer.echo(f"{name}: {len(rows)} (first: {', '.join(rows[:5])})")
            return 0 if rep.clean else 1
        finally:
            await engine.dispose()

    raise typer.Exit(asyncio.run(run()))


@app.command("cert-info")
def cert_info(certfile: Annotated[Path, typer.Argument(help="PEM certificate")]) -> None:
    """Days until a certificate expires (exit code 1 when under the warning threshold; use it from monitoring)."""
    from tt_server.settings import get_settings
    from tt_server.tls import days_until_expiry

    left = days_until_expiry(certfile)
    typer.echo(f"{certfile}: {left:.1f} days left")
    if left < get_settings().cert_expiry_warn_days:
        raise typer.Exit(1)


async def _serve(
    config: Any, host: str, redirect_port: int | None, https_port: int, settings: Any = None
) -> None:
    import uvicorn

    servers = [uvicorn.Server(config)]
    stop = asyncio.Event()
    reload_task = None
    if config.ssl_certfile and settings is not None:
        from tt_server.tls import CertReloader

        config.load()  # builds config.ssl; the reloader swaps the chain on that live context
        reloader = CertReloader(
            config.ssl, config.ssl_certfile, config.ssl_keyfile,
            interval=settings.cert_reload_interval_s, warn_days=settings.cert_expiry_warn_days,
        )  # fmt: skip
        reload_task = asyncio.create_task(reloader.run(stop))
    if redirect_port:
        from tt_server.tls import redirect_app

        servers.append(
            uvicorn.Server(uvicorn.Config(redirect_app(https_port), host=host, port=redirect_port))
        )
    try:
        await asyncio.gather(*(srv.serve() for srv in servers))
    finally:
        stop.set()
        if reload_task:
            await reload_task


@seed_app.command("demo")
def seed_demo(reset: bool = typer.Option(False, "--reset", help="Truncate all tables first")) -> None:
    """Create the `acme` demo workspace from tools/gen_snapshot.py (deterministic ids)."""
    typer.echo(asyncio.run(_seed(reset)))


if __name__ == "__main__":
    app()
