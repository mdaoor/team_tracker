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


# Tables `commit_bookkeeping()` may carry: infrastructure rows that are not domain facts. Anything else touched in the unit of
# work (a work item, a project, a membership ...) makes it raise, so a mid-workflow bookkeeping commit can never smuggle a
# domain change past the Event guard. Extending this set is a reviewed change (see tests/unit/test_commit_allowlist.py).
BOOKKEEPING_TABLES = frozenset(
    {
        "api_tokens",  # last_used_at
        "login_attempts",  # sign-in backoff
        "people",  # the governance bot actor
        "policy_templates",  # shipped reference data
        "habit_definitions",
        "presentation_templates",
        "jira_links",  # sync state, logs and worklog ids
        "jira_sync_log",
        "jira_worklogs",
    }
)


class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self.events = EventWriter()
        self._touched_projection = False
        self._touched_tables: set[str] = set()
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
        self._touched_tables = set()
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
            self._touched_tables.add(getattr(obj, "__tablename__", type(obj).__name__))

    async def commit_bookkeeping(self) -> None:
        """Commit rows that are infrastructure rather than domain facts (the governance bot person, an API token's
        `last_used_at`, Jira link logs) without requiring an Event.

        Raises `MissingEventError` when anything outside `BOOKKEEPING_TABLES` was touched in this unit of work (pending or
        already flushed): bookkeeping may only carry bookkeeping, never a leaked domain change. Call it before the real work starts, or after it has
        been committed through `commit()`.
        """
        await self.session.flush()
        leaked = self._touched_tables - BOOKKEEPING_TABLES
        if leaked:
            await self.session.rollback()
            raise MissingEventError(
                f"commit_bookkeeping() would commit domain tables {sorted(leaked)} without an Event; "
                "emit the event and use commit()"
            )
        await self.session.commit()
        self._touched_projection = False
        self._touched_tables = set()

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
        self._touched_tables = set()


async def commit_derived(session: AsyncSession) -> None:
    """Commit work that is derived or housekeeping and has no Event by design (a snapshot cache row, a retention sweep).

    The one place outside `UnitOfWork` that commits; `tests/unit/test_architecture.py` fails on any other `.commit()` call.
    """
    await session.commit()
