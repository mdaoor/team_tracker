# ADR-0011: The prototype's section renderers are the spec; changes are additive
Date: 2026-10-24   Status: accepted

## Decision
`docs/spec/team_tracker_presentation.html` defines what each section shows (KPI tiles, project cards, pipeline columns, explorer,
radar, Gantt, now, people, violations & habits) and the shape of its data object `D`. The snapshot (`GET /snapshot`,
`docs/api/snapshot-schema.json`) reproduces `D` field for field; the deck renderer (`apps/presentation`) ports each section function to
`render<Kind>(ctx, config, slide) -> html`. Changes are additive only: new optional snapshot fields, new slide kinds, new config keys.
Removing or re-typing a field, or changing a section's arithmetic, needs a new ADR and a `schema_version` bump.

## How it is enforced
* The golden test (`tests/seeded/test_snapshot_seeded.py`) compares the assembler with `tools/gen_snapshot.py` for every seed-controlled field.
* `e2e/visual.spec.ts` renders page mode and the prototype side by side in both themes and fails above 2 % differing pixels (measured: 0.02 %).
* `exports/compute.py` holds the same arithmetic for PPTX/CSV; `renderers.test.js` pins the golden numbers (8 open, 24 active, 36 violations ...).

## Consequences
Fixes to a prototype formula land in both the JS renderer and `compute.py`. Pagination ("(cont.)") and the table toggle are additions the
prototype lacks; they do not change numbers.
