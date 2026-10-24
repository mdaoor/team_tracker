# Phase 6 — Query API, filter grammar and views

## Goal
One filter grammar (`SavedView.filter`, object model §9) compiles to SQL and powers every layout: list/table, board, pipeline, matrix, gantt, calendar, now, and the drill‑down breadcrumb model. Views are `SavedView` rows and shareable URLs. The same compiler is reused by the governance engine's `candidate_filter` (P8).

## Depends on
Phase 4 (ACL), Phase 3 (dimensions, placements, weights), Phase 5 (shell).

## Inputs
- Object model §9 (filter JSON, layouts, derived views table), §13.4 (weight scheme per report)
- UX §4 (all), §7.3 steps 3–4

## Scope
1. `packages/filter_grammar` (`tt_filter`): JSON filter → validated AST → SQLAlchemy `Select` over `work_items` (and other entities later), with ACL hook; property‑based tests; JSON Schema for the grammar (served to the UI for validation).
2. Query API: `POST /query/items` (filter + group_by + sort + layout + weight_scheme + page), `POST /query/aggregate` (measure × dimension(s)), `GET/POST /views` (`SavedView` CRUD, share).
3. Layout endpoints returning layout‑shaped data: board (workflow states of one project), pipeline (stages of a process), matrix (dim × dim × measure), gantt (bars planned/actual/baseline + dependencies), calendar (due/planned_end/milestones by day), now (active activities).
4. Web: Views area with the layout tabs, filter chips with negation and typeahead over `DimensionValue`s, group‑by, sort, weight scheme selector, save/share view, drill‑down breadcrumb (`Workspace › Product › Project › Epic › N items`, Backspace up — UX §4.3), card anatomy (UX §4.2), stage‑age colouring, every number → list link.
5. Exports: CSV for list/table and matrix (stamped per P4).

## Out of scope
Summary layout / presentation page (P7), violations and habits layouts (P9).

## Detailed tasks

### 6.1 Filter grammar
AST node types and SQL mapping (keys from object model §9 example, extended minimally):

| key | shape | SQL |
|---|---|---|
| `dims` | `{<dimension_key>: "VALUE" \| {"in": [...]} \| {"not_in": [...]} \| {"is_null": true} \| {"under": "VALUE"}}` | `EXISTS (classifications c JOIN dimension_values v …)`; `under` uses the value tree (`ltree` path on `dimension_values`, add column `path` in migration) |
| `people` | `{"assignee": "@h" \| [..], "assignee_in_team": "slug", "reporter": …, "assignment_role": {"role": "reviewer", "person": "@h"}}` | joins on `work_items.assignee_id`, `memberships`, `assignments` |
| `people2` | `{"reports_to": "@h", "kind": ["line","project"]}` | `assignee_id IN reports_of(@h, kinds)` (P4 resolver as a SQL CTE) |
| `priority` | `{"gte"\|"lte"\|"eq"\|"in": …}` | ordinal mapping of the `Priority` enum |
| `weight` | `{"gte": n, "scheme": "key"?}` | join `weights` for the chosen scheme (default: request's `weight_scheme_id`) |
| `impact` | `{"gte": n}` | column |
| `due_date` / `planned_end` / `planned_start` / `actual_end` / `created` | `{"between": [d1, d2]} \| {"before": d} \| {"after": d} \| {"is_null": true}`; relative tokens `today, tomorrow, -14d, +7d, start_of_week, end_of_month` | resolved server‑side in the actor's zone |
| `touched` | `{"since": "-14d"}` | `EXISTS events WHERE entity=item AND occurred_at ≥` (materialised `work_items.last_touched_at` maintained by event hook — add column) |
| `state` | `{"category": ["open","active"]} \| {"in": [state_keys]}` | join `workflow_states` |
| `stage` | `{"in": [...], "process": "key", "age_gt_expected": true}` | `stage_placements WHERE exited_at IS NULL` |
| `type` | `{"in": [...]}` | join types |
| `tags` | `{"all": [...], "any": [...], "none": [...]}` | `tag_links` |
| `risk` | `{"open_score_gte": n, "linked": "KEY"}` | `risk_links` + `risks` |
| `violations` | `{"open": true, "category": [...], "severity_gte": "warning"}` | `violations` (table exists from P0; empty until P8) |
| `text` | `"login"` | `title ILIKE` or trigram `%` |
| `project` | `{"in": ["WEB"]}` shorthand for `dims.project` | — |
| `include_inbox` | bool, default false | ACL also enforces |
| `and` / `or` / `not` | arrays / object of sub‑filters | boolean composition; depth ≤ 4 |

Compiler rules: everything is parameterised; unknown keys → `400` with the JSON pointer; dimension keys validated against the workspace's `Dimension`s; output `Select` gets `acl.visible()` applied last. `explain()` returns the SQL for debugging (admin only).

### 6.2 Query API
- `POST /query/items {filter, group_by[], sort[], layout, weight_scheme_id?, page{cursor,size≤200}, fields[]}` → `{items[], groups[], page}`; `items` carry the card fields: key, title, project, product, type, assignee, team, stage + `stage_age_days` + `expected_duration_days`, state, priority, weight (chosen scheme), impact, estimate, remaining, hours_logged, planned/actual dates, tags, top risk score, open violation count, last_touched.
- `POST /query/aggregate {filter, rows: dim_key, cols?: dim_key, measure: count|hours|weighted_throughput|sum_weight|open_violations|avg_stage_age|remaining_estimate, weight_scheme_id?}` → `{rows[], cols[], cells[][], totals}`; `tags` as a dimension is multi‑valued (an item counts in each tag).
- `SavedView` CRUD: `filter` validated by the grammar schema; `is_shared` → readable by anyone with `view.read` in `scope`; share link = `/v/{view_id}`; URL state `?f=`, `?layout=`, `?g=`, `?s=`, `?ws=` round‑trips to a `SavedView` payload.

### 6.3 Layout endpoints
- **Board** `GET /projects/{key}/board?filter=` → columns = workflow states (with `wip_limit`, counts), cards; drag = `PATCH /items/{key} {state_id}`.
- **Pipeline** `GET /processes/{key}/pipeline?filter=` → columns = stages (expected duration, `wip_limit`, `wip_count`, `over_expected` count, Σ weight), cards; drag = `PUT /items/{key}/stage`; pending approval shown.
- **Matrix** = aggregate; click cell → list with the two dims added to the filter.
- **Gantt** `POST /query/gantt {filter, rows: epic|item|milestone, baseline?}` → rows with `planned_start/end`, `actual_start/end`, baseline ghost from `Baseline{baseline_name}`, dependency arrows from `WorkItemLink{finish_to_start|…|depends_on}` with `lag_days`, milestones as diamonds, today line, roll‑up bars for parents (min start / max end of subtree).
- **Calendar** `POST /query/calendar {filter, from, to, fields: [due_date, planned_end, milestones, iterations]}` → events by day.
- **Now** `GET /now?scope=` → active `Activity` cards (person, item, project, kind, since, note redacted per ACL).

### 6.4 Web views
- Layout tabs: list · board · pipeline · matrix · gantt · calendar · now (summary added in P7).
- Filter chips: `+ Filter` opens a dimension picker (builtin + custom + people/dates/state/tags/risk/violations); each chip editable, negatable (`¬`), removable; group‑by is a chip; everything serialised to `?f=`.
- Table: column chooser, inline edit for managers (same controls as the drawer), virtual scroll, CSV export.
- Board/pipeline: `@dnd-kit`, optimistic move, WIP breach highlight (border `--serious` as in the prototype), stage‑age colouring.
- Matrix: heat‑map with `--seq1..6` scale (reuse the prototype's `seq()`), row/col totals, click → drill.
- Gantt: SVG, zoom (day/week/month), dependency arrows, baseline ghosts, variance tooltip (planned vs actual days).
- Calendar: month/week; drag an item to a day → `planned_end` update (overlay permission).
- Now: cards with live elapsed time (poll 30 s; SSE later).
- Drill‑down: `useDrillPath()` pushes `{label, filterDelta}`; breadcrumb chips; Backspace pops; filter chips update so the user can fork (UX §4.3).
- Card anatomy per UX §4.2 (key · priority dot · weight · risk chip / title / assignee · stage age · time / tags · milestone).

### 6.5 Tests
- Grammar: Hypothesis generates random valid filters → compiles without error, SQL executes on seed, result ⊆ unfiltered; invalid filters → 400 with pointer; fuzz for injection (strings with quotes/semicolons never alter SQL structure — assert via `str(select)` parameter count).
- Golden queries against the seed: `dims.stage in [build,test] and people.assignee_in_team squad-b` → the exact keys from `gen_snapshot.py`; `violations.open` is empty pre‑P8 (test is marked to flip in P8).
- Playwright: UX §7.3 steps 3–4 (click pipeline `test` → list → open WEB‑101 → see stage age/risk; switch matrix to team × stage; copy URL, open in new context → same view).

## Acceptance criteria
- [ ] Every example key in object model §9's filter JSON is supported and tested.
- [ ] A view URL opened by another user shows only what they may see (ACL applied inside the compiler, verified by test).
- [ ] p95 < 300 ms for `POST /query/items` on 50 k items / 200 k classifications (synthetic load fixture `tools/seed/load.py`), with `EXPLAIN` documented.
- [ ] Pipeline shows GOV/build WIP breach (7 > 4) from the seed; stage ages colour at expected and 2×.
- [ ] Gantt renders epics with planned spans, milestones, baseline ghost after `POST /projects/WEB/baselines`.
- [ ] CSV export is stamped with viewer/scope/time.
- [ ] Changing the weight scheme selector changes Σ weight and weighted‑throughput numbers without changing `WorkItem.weight` (§13.4).

## Exit checklist
- [ ] `docs/api/filter-grammar.md` with the table above and the JSON Schema.
- [ ] `tt_filter` published as a workspace package; P8 imports it for `candidate_filter`.
- [ ] ADR‑0010 "Relative date tokens and time zone resolution".

## Handover notes
P7's snapshot endpoint is a bundle of aggregate + layout queries; build it on `tt_filter` and the layout services, not on new SQL.
