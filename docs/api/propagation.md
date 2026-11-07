# Propagation (object model §6)

Rules run inline, in the transaction of the triggering change, and write `Event{source=propagation}` whose `correlation_id`
is the trigger's. They compare before writing, so running them again produces no events.

| trigger | rule | blocked by |
|---|---|---|
| `PUT /products/{p}/priority` | delivering projects (`ProductLink.role=delivers`) -> their items | project priority set manually; item `priority_source=manual` |
| `PUT /projects/{k}/priority` | root items of the project, then each subtree | item `priority_source=manual` (its own subtree still follows it) |
| `item.priority_changed` (manual) | subtree via `parent_id` | child `priority_source=manual` |
| `risk.linked` on a project / product / item | `RiskLink{inherited=true}` on every item in scope | an existing explicit link stays explicit |
| `risk.unlinked` | removes *inherited* links below the item; explicit child links stay | - |
| item created / project changed | mirrors, product + objective classifications, parent/project priority, default stage, inherited risks, weight | explicit priority, manual classifications |
| stage moved onto/off a done stage | state complete/reopen (ADR-0005) | - |
| `project_member.added` | emits only; read access arrives with Phase 4 | - |

## Worked example
```
PUT /projects/GOV/priority {"priority":"critical"}      -> correlation c1
  classification  project GOV / priority = critical      (manual)
  item.priority_changed GOV-10 (epic)  low->critical     source=propagation, correlation c1, priority_source=inherited
  item.priority_changed GOV-101 ...                      source=propagation, correlation c1
  GOV-105  priority_source=manual                        kept (counted in overrides_kept)
PUT again with the same priority                          -> items_changed 0, no item events
```
Weights are recomputed for every item whose inputs changed; manual weights are skipped.
Roll-ups (`GET /items/{k}/rollup`) are computed on read for subtrees of up to 500 items.

Deviation from the plan: propagation is synchronous, not a Procrastinate job, so a client sees consistent state when its request
returns. Subtrees above a few thousand items will need the job form (Phase 12).
