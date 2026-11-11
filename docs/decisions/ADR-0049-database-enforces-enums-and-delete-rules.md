# ADR-0049: CHECK constraints for enums, explicit ON DELETE for every foreign key
Date: 2026-10-25   Status: accepted (Phase 13 R4.4; review T15)

## Decision
Migration `0012` adds `CHECK (col IS NULL OR col IN (…))` to all 96 `Enum(native_enum=False)` columns (VARCHAR storage is kept: adding a
value stays an app change plus a one-line migration, not an `ALTER TYPE`), and rewrites each foreign key with an explicit rule: `CASCADE` for
rows that only exist as part of their parent (checklist items, risk links/actions, approval decisions, custom-field values, weights,
assignments, estimates, baselines, work-item links, stage transition rules, key sequences), `RESTRICT` for everything else.
Two tests guard it: every non-native enum column has its check, and no foreign key is left at `NO ACTION`.

## Hard deletes that remain (reviewed, kept)
* `governance/apply.py` and `governance/metrics.py`: replace derived rows (open violations being recomputed, metric snapshots) — they are
  recomputable output, not history.
* `domain/overlay/risks.py`: unlinking a risk from an item deletes the join row (the event keeps the fact).
* `api/routers/webauth.py`: login-attempt counters are cleared on success.
Everything a person authored is soft-deleted.

## Consequences
Models do not repeat `ondelete=`; the database is the authority and the tests read it from `pg_catalog`.
