"""Reference data the product ships (Phase 13 R4.3, review T4): policy templates, habit definitions, presentation templates.

Loaded once by `tt-admin migrate` and on application start, never inside a GET handler. Idempotent: unchanged rows are left alone.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tt_server.db.uow import UnitOfWork
from tt_server.domain import presentation_templates as ptpl
from tt_server.governance import service as governance


async def seed_reference_data(factory: async_sessionmaker[AsyncSession]) -> int:
    """Upsert every shipped built-in; returns how many presentation templates changed."""
    async with UnitOfWork(factory) as uow:
        await governance.ensure_builtins(uow.session)
        changed = await ptpl.ensure_builtins(uow.session)
        await uow.commit_bookkeeping()  # shipped reference data is not a domain fact: no Event
    return changed
