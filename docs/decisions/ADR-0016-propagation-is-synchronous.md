# ADR-0016: Propagation runs synchronously in the triggering transaction
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
Overlay propagation (priority, risks, mirrors, stage/state sync, team and process changes) runs inline in the transaction of the change and writes `source=propagation` events with the trigger's correlation id, instead of a Procrastinate job.

## Why
A caller sees a consistent state when its request returns; the idempotent compare-before-write rules make a re-run emit nothing; there is no window in which a policy or a snapshot reads half-propagated data.

## Consequences
Request latency grows with the subtree (documented ceiling: a few thousand items). Above that the rules must move to the job form (`work_item_rollups`, Phase 12 hardening). The rules are written as pure functions of rows so the move does not change behaviour.
