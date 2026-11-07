# ADR-0005: Stage -> state category defaults and the sync rules
Date: 2026-10-23   Status: accepted

## Context
`Stage.maps_to_state_category` is required (object model §13.1) but the spec gives no default mapping.

## Decision
Builtin process `sw-delivery`: idea, discovery, design -> `open`; build, test, staging -> `active`; production, retired -> `done`
(categories ideation, planning, planning, execution, validation, release, operate, closed).
Sync is asymmetric by design: only crossing the `done` boundary changes anything.
- Placing an item on a done-mapped stage completes it (terminal state, `closed_at`); leaving one reopens it (`item.reopened`).
  Both writes are `source=propagation` with the `stage.moved` event as correlation.
- A person marking the item done moves its placement to the first `closed` stage (`retired`); reopening returns it to the most
  recent earlier stage that is not done-mapped, else the first `execution` stage.
- A per-transaction `sync_depth` counter in `UnitOfWork.context` prevents ping-pong between the two directions.
Intermediate stages never touch the state.

## Consequences
Boards on either axis stay consistent without a second source of truth. A workflow with several done states uses its terminal one.
