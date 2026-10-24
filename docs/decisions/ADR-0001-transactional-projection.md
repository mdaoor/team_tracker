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
