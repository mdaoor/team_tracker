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
        return f"seeded {count} rows into workspace 'acme'"
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


@seed_app.command("demo")
def seed_demo(reset: bool = typer.Option(False, "--reset", help="Truncate all tables first")) -> None:
    """Create the `acme` demo workspace from tools/gen_snapshot.py (deterministic ids)."""
    typer.echo(asyncio.run(_seed(reset)))


if __name__ == "__main__":
    app()
