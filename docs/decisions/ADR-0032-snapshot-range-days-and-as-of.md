# ADR-0032: The snapshot range is `days` + `as_of`, cached per viewer
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
A snapshot covers `[as_of - days, as_of]`; the cache key is (person, scope, days, scheme).

## Why
A deck is a statement about a window ending at a moment; `as_of` makes it reproducible (golden tests, exports).

## Consequences
No arbitrary from/to ranges. Cache rows are per viewer and dropped when a project's visibility changes.
