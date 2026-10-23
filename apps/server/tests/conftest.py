"""Test harness: Postgres via testcontainers (or TT_TEST_DATABASE_URL), migrated once per session."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tt_server.db.uow import UnitOfWork
from tt_server.jobs.schema import ensure_procrastinate_schema
from tt_server.main import create_app
from tt_server.settings import Settings

SERVER_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """SQLAlchemy URL of an empty-but-extension-capable Postgres 16 database."""
    explicit = os.environ.get("TT_TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        yield pg.get_connection_url()


def alembic_config(url: str) -> Config:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> str:
    from sqlalchemy import create_engine

    sync = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with sync.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    sync.dispose()
    command.upgrade(alembic_config(database_url), "head")
    ensure_procrastinate_schema(database_url.replace("+psycopg", "", 1))
    return database_url


@pytest.fixture(scope="session")
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_conn(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside an outer transaction that is rolled back after each test."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        yield conn
        await trans.rollback()


@pytest.fixture
def session_factory(db_conn: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def uow(session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWork:
    return UnitOfWork(session_factory)


@pytest.fixture
async def client(
    engine: AsyncEngine, migrated_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[httpx.AsyncClient]:
    """API client whose requests run inside the per-test rolled-back transaction (dev auth on)."""
    settings = Settings(database_url=migrated_url, log_json=False, log_level="WARNING", dev_auth=True)
    app = create_app(settings, engine=engine, session_factory=session_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
