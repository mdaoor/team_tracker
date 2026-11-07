# ADR-0030: The filter compiler lives in the server package; `under` is a recursive CTE; `touched` is `EXISTS events`
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
`tt_filter` holds the grammar (parse, validate, schema, relative dates). SQL compilation is in `tt_server/query/compile.py` because it needs the models and the ACL.

## Why
`dims.under` uses a recursive CTE over dimension values instead of an `ltree` column, because dimension values are shallow and the CTE avoids a second hierarchy to keep in sync. `touched` uses `EXISTS (events ...)` instead of a maintained `last_touched_at`, which would need a write on every event.

## Consequences
Both are index-friendly at the measured sizes (50 k items). If `touched` becomes hot, add the column with a trigger-free updater job.
