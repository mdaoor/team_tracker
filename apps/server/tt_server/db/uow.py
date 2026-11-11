"""UnitOfWork: one transaction = projection rows + Event rows + outbox jobs (architecture §4.1)."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
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


# Row-level narrowing of the table allow-list (ADR-0001 amendment): for these tables `commit_bookkeeping()` may *modify* an
# existing row only in the listed columns (plus `updated_at`); creating rows is always allowed. An empty set means "rows may be
# created here but never edited". Tables not listed here are infrastructure rows whose every column is bookkeeping.
BOOKKEEPING_COLUMNS: dict[str, frozenset[str]] = {
    "api_tokens": frozenset({"last_used_at"}),  # never scopes, expiry, owner or hash
    "people": frozenset(),  # the governance bot is created, nobody is edited
    "jira_worklogs": frozenset(),
    "jira_links": frozenset(
        {
            "last_pushed_hash",
            "last_state_key",
            "last_pushed_at",
            "last_inbound_at",
            "status",
            "attempts",
            "last_error",
        }
    ),  # sync state, never which item or issue the link joins
}
_ALWAYS_ALLOWED_COLUMNS = frozenset({"updated_at"})


class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self.events = EventWriter()
        self._touched_projection = False
        self._touched_tables: set[str] = set()
        self._modified_columns: dict[str, set[str]] = {}  # table -> columns changed on existing rows
        self._session: AsyncSession | None = None
        # Ambient data for hooks (actor_id, correlation_id, sync_depth ...); reset on every `async with`.
        self.context: dict[str, Any] = {}
        # While set, `commit()` and `commit_bookkeeping()` only flush: the holder (the command executor) makes the one real
        # commit, so a multi-step command is atomic however many router functions it calls.
        self.hold_commits = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside 'async with'")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        self._touched_projection = False
        self._touched_tables = set()
        self._modified_columns = {}
        self.context = {}
        self.hold_commits = False
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
            table = getattr(obj, "__tablename__", type(obj).__name__)
            self._touched_tables.add(table)
            if obj in session.deleted:
                self._modified_columns.setdefault(table, set()).add("<deleted>")
            elif obj in session.dirty:
                changed = {a.key for a in sa_inspect(obj).attrs if a.history.has_changes()}
                self._modified_columns.setdefault(table, set()).update(changed)

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
        edited = {
            f"{t}.{c}"
            for t, cols in self._modified_columns.items()
            if t in BOOKKEEPING_COLUMNS
            for c in cols - BOOKKEEPING_COLUMNS[t] - _ALWAYS_ALLOWED_COLUMNS
        }
        if edited:
            await self.session.rollback()
            raise MissingEventError(
                f"commit_bookkeeping() would commit edits to {sorted(edited)} without an Event; "
                "only the bookkeeping columns of these tables may change this way"
            )
        if self.hold_commits:
            return
        await self.session.commit()
        self._touched_projection = False
        self._touched_tables = set()
        self._modified_columns = {}

    async def commit(self, *, allow_unlogged: bool = False) -> None:
        """Flush projection rows, append buffered events, enqueue jobs, commit.

        Raises `MissingEventError` when projection tables changed but no event was appended;
        `allow_unlogged=True` is the allow-list for the seed path.
        """
        session = self.session
        await session.flush()
        if self.hold_commits:
            return  # the holder commits once, after every step; the Event guard runs then
        if self._touched_projection and len(self.events) == 0 and not allow_unlogged:
            await session.rollback()
            raise MissingEventError("projection tables changed without an Event in the same unit of work")
        flushed = self.events.flush(session)
        await session.flush()
        await outbox.dispatch(session, flushed)
        await session.commit()
        self._touched_projection = False
        self._touched_tables = set()
        self._modified_columns = {}


async def commit_derived(session: AsyncSession) -> None:
    """Commit work that is derived or housekeeping and has no Event by design (a snapshot cache row, a retention sweep).

    The one place outside `UnitOfWork` that commits; `tests/unit/test_architecture.py` fails on any other `.commit()` call.
    """
    await session.commit()
