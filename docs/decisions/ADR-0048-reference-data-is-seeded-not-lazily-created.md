# ADR-0048: Built-ins are seeded at migrate/start; the only commits are the unit of work's
Date: 2026-10-25   Status: accepted (Phase 13 R4.3; review T4)

## Context
GET handlers called `ensure_builtins()` and then `session.commit()`; a dozen other paths committed a raw session, bypassing the
`MissingEventError` guard.

## Decision
* `bootstrap.seed_reference_data` loads policy templates, habit definitions and presentation templates. `tt-admin migrate` runs it; the
  application runs it at start (`TT_SEED_REFERENCE_ON_START`, default on). No request handler seeds anything. Tests seed the migrated
  database once and re-seed after the committed-engine fixture truncates.
* The only places that commit are `UnitOfWork.commit()` (domain facts, with events), `UnitOfWork.commit_bookkeeping()` (reference data,
  releasing a read transaction) and `db.uow.commit_derived()` (snapshot-cache rows, retention sweeps, insight housekeeping: derived data
  with no event by design). `tests/unit/test_architecture.py` greps for any other `.commit()`.

## Consequences
A database restored without reference data shows empty template lists until the next start or `tt-admin migrate`.
