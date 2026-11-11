# ADR-0045: The gateway holds budget before a call and settles it after; money is Decimal
Date: 2026-10-25   Status: accepted (Phase 13 R3.8; review T14)

## Context
The daily budget was checked, the model called, and the cost charged afterwards: N concurrent requests all passed the check at
the boundary. Costs were floats summed into a `Numeric(18,6)` column.

## Decision
`budgets.reserve_usd_per_request` (default 0.01) is held atomically (`Storage.reserve`: one `INSERT … ON CONFLICT DO UPDATE …
WHERE usd + hold <= limit`, or a lock in memory) before the call, then replaced by the real cost (`_settle`); a failed call gives the
hold back. A hold of 0 restores the old check. All amounts are `Decimal`; floats from providers enter through `Decimal(str(x))`.

## Consequences
A day's budget can be exceeded by at most one call's overrun beyond its hold. Tests: six parallel calls at the boundary → one passes;
hold released on failure; ten additions of 0.1 equal 1.0.
