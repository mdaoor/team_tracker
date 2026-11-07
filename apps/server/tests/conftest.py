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

# Hermetic settings (Phase 13 R3.2, review T2): a developer's .env or exported TT_* variables must not change what a test
# asserts about configuration. Tests that need a setting pass it explicitly or monkeypatch it.
Settings.model_config["env_file"] = None
_KEEP_ENV = {"TT_TEST_DATABASE_URL", "TT_CHROMIUM", "TT_E2E_ADMIN_URL"}


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [k for k in os.environ if k.startswith("TT_") and k not in _KEEP_ENV]:
        monkeypatch.delenv(name, raising=False)


def _worker() -> str | None:
    w = os.environ.get("PYTEST_XDIST_WORKER")
    return w if w and w != "master" else None


def _admin_engine(url: str):  # type: ignore[no-untyped-def]
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    return create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _db_name(url: str) -> str:
    from sqlalchemy.engine import make_url

    return str(make_url(url).database)


def _with_db(url: str, name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _template_ready(admin, name: str, stamp: str) -> bool:  # type: ignore[no-untyped-def]
    with admin.connect() as c:
        got = c.execute(
            text("select shobj_description(oid, 'pg_database') from pg_database where datname = :n"),
            {"n": name},
        ).scalar()
    return got == stamp


def _build_template(base_url: str, name: str, stamp: str, build) -> None:  # type: ignore[no-untyped-def]
    """Build `name` once per pytest run (the xdist run uid), under a file lock so parallel workers wait instead of racing."""
    import fcntl
    import tempfile

    admin = _admin_engine(base_url)
    lock = Path(tempfile.gettempdir()) / f"{name}.lock"
    with lock.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            if not _template_ready(admin, name, stamp):
                with admin.connect() as c:
                    c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                    c.execute(text(f'CREATE DATABASE "{name}"'))
                build(_with_db(base_url, name))
                with admin.connect() as c:
                    c.execute(text(f"COMMENT ON DATABASE \"{name}\" IS '{stamp}'"))
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    admin.dispose()


def _clone(base_url: str, name: str, template: str) -> str:
    admin = _admin_engine(base_url)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
    admin.dispose()
    return _with_db(base_url, name)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """SQLAlchemy URL of an empty-but-extension-capable Postgres 16 database (one per xdist worker)."""
    explicit = os.environ.get("TT_TEST_DATABASE_URL")
    if explicit:
        if w := _worker():
            yield _with_db(
                explicit, f"{_db_name(explicit)}_{w}"
            )  # created from the migrated template by `migrated_url`
            return
        yield explicit
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        yield pg.get_connection_url()


def alembic_config(url: str) -> Config:
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _migrate_into(url: str) -> None:
    from sqlalchemy import create_engine

    sync = create_engine(url, isolation_level="AUTOCOMMIT")
    with sync.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    sync.dispose()
    command.upgrade(alembic_config(url), "head")
    ensure_procrastinate_schema(url.replace("+psycopg", "", 1))
    _seed_reference(url)


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> str:
    if w := _worker():
        base = os.environ["TT_TEST_DATABASE_URL"]
        stamp = os.environ.get("PYTEST_XDIST_TESTRUNUID", "run")
        tpl = f"{_db_name(base)}_tpl"
        _build_template(base, tpl, stamp, _migrate_into)
        return _clone(base, f"{_db_name(base)}_{w}", tpl)
    _migrate_into(database_url)
    return database_url


def _seed_reference(url: str) -> None:
    """What `tt-admin migrate` does after the schema: load the shipped built-ins (committed, so every test sees them)."""
    import asyncio

    from tt_server.bootstrap import seed_reference_data

    async def run() -> None:
        eng = create_async_engine(url)
        try:
            await seed_reference_data(async_sessionmaker(eng, expire_on_commit=False))
        finally:
            await eng.dispose()

    asyncio.run(run())


@pytest.fixture(scope="session")
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_url, connect_args={"options": "-c jit=off"})
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
    settings = Settings(
        database_url=migrated_url,
        log_json=False,
        log_level="WARNING",
        dev_auth=True,
        rate_capture_per_min=0,
        rate_auth_per_min=0,
    )
    app = create_app(settings, engine=engine, session_factory=session_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- committed engine (concurrency tests)

_KEEP_TABLES = ("alembic_version",)


async def truncate_all(engine: AsyncEngine) -> None:
    """Empty every application table (not the schema, alembic or procrastinate bookkeeping)."""
    async with engine.begin() as conn:
        names = [
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "select tablename from pg_tables where schemaname = 'public' "
                        "and tablename not like 'procrastinate%' and tablename <> all(:keep)"
                    ),
                    {"keep": list(_KEEP_TABLES)},
                )
            ).all()
        ]
        if names:
            await conn.execute(
                text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE")
            )


async def reseed_reference(engine: AsyncEngine) -> None:
    from tt_server.bootstrap import seed_reference_data

    await seed_reference_data(async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture
async def committed_client(engine: AsyncEngine, migrated_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """For tests that run requests *concurrently*: real committing sessions on separate connections (the per-test savepoint
    connection cannot be shared by parallel requests). Everything is truncated afterwards, so tests stay independent."""
    await truncate_all(engine)
    await reseed_reference(engine)
    settings = Settings(
        database_url=migrated_url, log_json=False, log_level="WARNING", dev_auth=True,
        rate_capture_per_min=0, rate_auth_per_min=0,
    )  # fmt: skip
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings, engine=engine, session_factory=factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await truncate_all(engine)
    await reseed_reference(engine)  # the next test (and the savepoint ones) still find the shipped built-ins


# --------------------------------------------------------------------------- seeded database (Appendix B `seed_workspace`)


def _seed_demo(url: str) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SERVER_DIR.parents[1]))
    from tools.seed.demo import persist_demo

    async def seed() -> None:
        eng = create_async_engine(url)
        try:
            await persist_demo(async_sessionmaker(eng, expire_on_commit=False))
        finally:
            await eng.dispose()

    asyncio.run(seed())


@pytest.fixture(scope="session")
def seeded_url(database_url: str, migrated_url: str) -> str:
    """A second, migrated database holding the `acme` demo workspace, created once per session through the services."""
    from sqlalchemy import create_engine

    if w := _worker():
        base = os.environ["TT_TEST_DATABASE_URL"]
        stamp = os.environ.get("PYTEST_XDIST_TESTRUNUID", "run")
        tpl, mig = f"{_db_name(base)}_seed_tpl", f"{_db_name(base)}_tpl"

        def build(
            url: str,
        ) -> None:  # the seeded template is a clone of the migrated one plus the demo workspace
            admin = _admin_engine(base)
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{_db_name(url)}" WITH (FORCE)'))
                c.execute(text(f'CREATE DATABASE "{_db_name(url)}" TEMPLATE "{mig}"'))
            admin.dispose()
            _seed_demo(url)

        _build_template(base, tpl, stamp, build)
        return _clone(base, f"{_db_name(base)}_{w}_seed", tpl)

    base, _, name = database_url.rpartition("/")
    seed_name = name.split("?")[0] + "_seed"
    admin = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{seed_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{seed_name}"'))
    admin.dispose()
    url = f"{base}/{seed_name}"
    command.upgrade(alembic_config(url), "head")
    ensure_procrastinate_schema(url.replace("+psycopg", "", 1))
    _seed_reference(url)
    _seed_demo(url)
    return url


@pytest.fixture(scope="session")
async def seed_engine(seeded_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(seeded_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def seed_conn(seed_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with seed_engine.connect() as conn:
        trans = await conn.begin()
        yield conn
        await trans.rollback()


@pytest.fixture
def seed_sf(seed_conn: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(seed_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
async def seed_client(
    seed_engine: AsyncEngine, seeded_url: str, seed_sf: async_sessionmaker[AsyncSession]
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=seeded_url,
        log_json=False,
        log_level="WARNING",
        dev_auth=True,
        rate_capture_per_min=0,
        rate_auth_per_min=0,
    )
    app = create_app(settings, engine=seed_engine, session_factory=seed_sf)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
