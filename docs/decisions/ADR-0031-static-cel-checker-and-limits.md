# ADR-0031: CEL conditions are type-checked by our own checker and bounded by static and runtime limits
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
`celpy` has no type environment and no step cap. `tt_cel` parses with lark, checks roots/fields/functions/types against the context schema, limits size (4 KB, 400 nodes, depth), replaces `matches()` with a bounded implementation (length cap, no nested quantifiers or back-references), and the engine enforces a wall-clock budget per evaluation (partial sweeps never auto-resolve).

## Why
Policies are authored by managers; a typo or a pathological expression must neither pass silently nor stall a worker (review findings 14).

## Consequences
A per-row timeout is not possible in-process; the budget is per sweep. A hostile `policy.admin` can still write an expensive but valid expression; the budget bounds the damage.
