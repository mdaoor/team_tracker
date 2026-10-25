# ADR-0040: `verify-projection` replaces `rebuild-projection`
Date: 2026-10-24   Status: accepted (deviation from plan P12 §4; the acceptance item "rebuild == current" is NOT met)

## Context
The plan assumed that, because every mutation has an Event, projection tables can be rebuilt by replaying events. In this implementation an
event's payload records the change (`{after: {key, title}}`, `{before, after}` for updates, ids for links), not the full row; the seed path
also writes without events by design. A replay would produce rows missing most columns, and pretending otherwise would be worse than not
having the tool.

## Decision
`tt-admin verify-projection <workspace>` checks that every projected fact has an event that explains it (items have `item.created`, time
entries have a matching `time.logged` with the same minutes, events reference existing actors) and exits 1 on drift. Recovery from drift is
point-in-time restore (`docs/runbooks/restore.md`). Making the log sufficient for replay is a larger change (full-row snapshots in
payloads or per-entity event sourcing) that needs a product decision about storage cost; it is recorded as an open question.

## Consequences
Plan acceptance "`rebuild-projection work_item` produces equal tables" is unmet and reported as such. Seeded demo data (written with
`allow_unlogged`) reports drift by design; run the check on real workspaces.
