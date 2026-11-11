# ADR-0001 — Transactional projection instead of replay event sourcing

- Status: accepted
- Date: 2026-10-23
- Phase: 0
- Source: `plans/00-architecture-and-stack.md` §4.1

## Context
Object model §1 says the append-only `Event` log is the source of truth and tables are projections. Full
replay-based event sourcing (rebuilding state by folding events) costs a lot in v1: every query model must be
reproducible from events, schema changes need event upcasting, and nothing in the spec requires time travel.

## Decision
Every state change writes an `Event` row **in the same database transaction** as the projection rows it changes.
`UnitOfWork.commit()` flushes projection rows, appends the buffered events (only `EventWriter` may insert into
`events`), enqueues follow-up Procrastinate jobs through the same session (transactional outbox), then commits.
`UnitOfWork.commit()` raises `MissingEventError` when projection tables changed and no event was appended; the
seed path opts out with `allow_unlogged=True`.

## Consequences
- No event is processed without having been committed, and none is lost (jobs are deferred in the same tx).
- The log is complete but is not replayed in v1; `rebuild_projection(entity_type)` stays a Phase 12 stretch goal,
  so tables must not hold business state that exists only outside `Event.payload`.
- Writes that bypass the ORM (Core `INSERT/UPDATE` through `session.execute`) are not seen by the guard; code review
  and the "single event writer" test cover that gap.
- `Event.event_type` must be listed in `docs/api/event-catalogue.md`; `EventWriter.append` rejects unknown types.

## Amendment (PR #11 review): the allow-list is pinned
`UnitOfWork.commit_bookkeeping()` raises `MissingEventError` when the unit of work touched any table outside `BOOKKEEPING_TABLES`
(API-token usage, sign-in backoff, the governance bot person, shipped reference data, Jira sync state), whether the rows are pending or
already flushed, so it cannot carry a domain change past the guard. `tests/unit/test_commit_allowlist.py` freezes the exact call sites
of `commit_bookkeeping`, `commit_derived` and `allow_unlogged=True`; adding one means editing that test.

## Amendment (handover item 11): the guard also looks at columns
A table-level allow-list still let a bookkeeping commit carry a *different* change to an allowed table (an `api_tokens` row's scopes
or expiry, a person's name). Cheap to close, so `UnitOfWork` now records, per table, which columns changed on existing rows (from the
ORM's attribute history in `before_flush`) and `commit_bookkeeping()` refuses an edit outside `BOOKKEEPING_COLUMNS`: `api_tokens`
may change `last_used_at`; `people` and `jira_worklogs` may gain rows but never be edited; `jira_links` may change its sync state
(hash, state key, push/inbound times, status, attempts, error) but not which item or issue it joins; a delete in these tables is
refused. Creating rows stays allowed, `updated_at` is ignored, and tables not listed (`login_attempts`, shipped reference data,
`jira_sync_log`) stay open in every column because every column of them is bookkeeping. Writes through Core `UPDATE` still bypass
the ORM history (the existing documented gap). Tests: `tests/unit/test_commit_allowlist.py`.
