"""UnitOfWork: one transaction = projection rows + Event rows + outbox jobs (architecture §4.1)."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, UOWTransaction

from tt_server.db.models.capture import Event as EventRow
from tt_server.events import outbox
from tt_server.events.writer import EventWriter


class MissingEventError(RuntimeError):
    """Projection rows changed but no Event was appended in the same unit of work."""


class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self.events = EventWriter()
        self._touched_projection = False
        self._session: AsyncSession | None = None
        # Ambient data for hooks (actor_id, correlation_id, sync_depth ...); reset on every `async with`.
        self.context: dict[str, Any] = {}

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside 'async with'")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        self._touched_projection = False
        self.context = {}
        event.listen(self._session.sync_session, "before_flush", self._on_before_flush)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        session = self.session
        try:
            await session.rollback()  # no-op after a successful commit
        finally:
            await session.close()
            self._session = None

    def _on_before_flush(self, session: Session, ctx: UOWTransaction, instances: object) -> None:
        for obj in (*session.new, *session.dirty, *session.deleted):
            if isinstance(obj, EventRow):
                continue
            if obj in session.dirty and not session.is_modified(obj):
                continue
            self._touched_projection = True

    async def commit(self, *, allow_unlogged: bool = False) -> None:
        """Flush projection rows, append buffered events, enqueue jobs, commit.

        Raises `MissingEventError` when projection tables changed but no event was appended;
        `allow_unlogged=True` is the allow-list for the seed path.
        """
        session = self.session
        await session.flush()
        if self._touched_projection and len(self.events) == 0 and not allow_unlogged:
            await session.rollback()
            raise MissingEventError("projection tables changed without an Event in the same unit of work")
        flushed = self.events.flush(session)
        await session.flush()
        await outbox.dispatch(session, flushed)
        await session.commit()
        self._touched_projection = False
