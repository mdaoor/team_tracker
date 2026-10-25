"""One database engine per worker process (Phase 13 R3.9): periodic jobs used to build and dispose an engine per run."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from tt_server.db.session import make_engine, make_session_factory
from tt_server.settings import get_settings

_engines: dict[tuple[str, int], AsyncEngine] = {}


def shared_engine() -> AsyncEngine:
    """The process-wide engine for the running event loop (an async engine must not cross loops)."""

    key = (get_settings().database_url, id(asyncio.get_running_loop()))
    eng = _engines.get(key)
    if eng is None:
        eng = _engines[key] = make_engine()
    return eng


def shared_factory() -> async_sessionmaker:  # type: ignore[type-arg]
    return make_session_factory(shared_engine())


async def dispose_shared() -> None:
    """Close every shared engine (worker shutdown, tests)."""
    engines = list(_engines.values())
    _engines.clear()
    for e in engines:
        await e.dispose()
