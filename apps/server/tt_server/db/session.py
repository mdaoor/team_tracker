"""Async engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from tt_server.settings import get_settings


def make_engine(url: str | None = None) -> AsyncEngine:
    # JIT compilation costs more than it saves on our short, wide queries (measured: 175 ms -> ~60 ms on a 50 k-row scan)
    return create_async_engine(
        url or get_settings().database_url, pool_pre_ping=True, connect_args={"options": "-c jit=off"}
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
