# Phase 3 — Management overlay: dimensions, process & stages, products, weights, propagation, risks, approvals, review queue API

## Goal
Managers (via API now, UI in P5) can enrich captured items without touching capture rows: classify into any dimension, place on a delivery stage with history, group under products/objectives, set priority/impact/weight per scheme, attach risks, require approvals. Propagation and stage ↔ state sync run server‑side and are audited. The capture pipeline from P1 now completes the full §12 sequence (inherited classifications → default stage → weights).

## Depends on
Phase 1 (and Phase 2 for the pack fields it fills).

## Inputs
- Object model §4, §5, §6, §7 (Risk/Approval additions), §11, §13 decisions 1, 2, 4, §14
- UX §3.3 (editor semantics), §3.4 (collisions), §7.2

## Scope
1. **Dimensions**: `Dimension`, `DimensionValue`, `Classification` CRUD; builtin dimensions auto‑provisioned per workspace (`project, product, stage, team, objective, priority, type, iteration, milestone, release, component`) with `DimensionValue.backing_type/backing_id` kept in sync by mirrors; custom dimensions (flat/tree, single/multi).
2. **Mirrors**: every builtin FK change on `WorkItem` (`project_id, team_id, iteration_id, milestone_id, release_id, component_id, type_id, priority`) and every current `StagePlacement` / `ProductLink` upserts the corresponding `Classification{source=rule, is_confirmed=true}` row. Implemented as domain hooks (not DB triggers) so they emit no extra events but are covered by tests.
3. **Process**: `Process`, `Stage`, `StagePlacement`, `StageTransitionRule`; builtin process `sw-delivery` (idea → discovery → design → build → test → staging → production → retired) with the expected durations from `gen_snapshot.py`; move API with exclusion‑constraint‑guaranteed single current placement; WIP count; stage ↔ state sync (§13.1).
4. **Products & objectives**: `Product` tree, `ProductLink`, `Objective`, `KeyResult` CRUD; `KeyResult.source=metric_snapshot` wiring stub (P8 fills).
5. **Weights**: `WeightScheme` formula evaluator, `Weight` rows per scheme, manual override, `value_score` derivation.
6. **Propagation engine** per §6 table + roll‑ups; `Event{source=propagation}`.
7. **Risks**: `Risk` at project/item/product level, `RiskLink` with `inherited`, `RiskAction`; `score` property; risk roll‑up (`open_risk_score`).
8. **Approvals**: `Approval`, `ApprovalDecision`, policies `any_one | all | quorum`; stage `requires_approval` creates an approval on entry and blocks the placement from becoming "settled" until approved (placement exists with `approval_id`, `Stage` entry is provisional until approval — model as `StagePlacement.approval_id` + the approval status; a pending approval shows as "pending" in views); `Milestone.approval_id` gating `completed_at`.
9. **Review queue API** over `review_items` (from P1) + the unconfirmed `Classification`s: list scoped by manager, resolve/confirm/ignore/merge actions, bulk; learned glossary write‑back (UX §3.2).
10. **Bulk edit API** with propagation preview (UX §3.3 last bullet).
11. **Provenance**: `GET /items/{key}/provenance` returns, per overlay field, `{set_by, set_at, source, inherited_from}` (UX §3.4).
12. Capture pipeline completion: on item creation → inherited classifications (project → product → objective), default stage (`Project.default_process_id` first stage, or `translator_rules`/pack default), weight computation; the LLM hints `stage`, `product`, `risks` are now applied (unconfirmed) instead of only queued.
13. Seed rewritten to go through services (removes the P0 TODO). The seed gains a **plan‑only project** `ERP` ("ERP Rollout": 3 phases, 9 deliverables, 4 milestones, dependencies, a baseline, **zero tasks/activities**) so every view, snapshot and deck is tested against a project with no reported work.
14. **Plan ↔ execution linkage** (§3.11): reporter tasks attach to the manager's deliverables; progress rolls up deliverable → phase → milestone → project.

## Out of scope
UI (P5), ACL (P4) — but every service takes an `actor` and records `set_by`.

## Detailed tasks

### 3.1 Dimension model & builtin provisioning
- On workspace creation (and a one‑off backfill command), create builtin `Dimension`s with `kind=builtin`, `value_type=tree` for `product`, `team`, `project` (projects nest), `flat` otherwise; `cardinality=single` except `objective` (multi).
- `DimensionValue` for builtin dims is created/updated/soft‑deleted whenever the backing row changes (`backing_type`, `backing_id`, `name`, `key`, `parent_value_id` from the backing tree). Service hook `mirrors.sync_value(backing)`.
- Custom dimension API: create (`key` unique, immutable), values with `position`, tree via `parent_value_id` (cycle check), bulk values (`POST …/values:bulk` with `["a","b"]`), deactivate.
- `is_required_for` JSON is stored and exposed to the governance engine (P8) and to the pack (`custom_fields`‑like `required_dimensions`).

### 3.2 Classification service
- `classify(target, dimension, value, *, source, set_by, confidence=None, is_confirmed=True, inherited_from=None)`:
  - single cardinality → replace existing row (soft‑delete old, insert new) and emit `classification.changed`; multi → add.
  - `applies_to` check against `target_type`.
  - manual classification sets `source=manual` and marks any inherited row for that dimension as overridden (`Classification` keeps both? **No**: keep one effective row per single‑cardinality dimension; record the override fact in `Event.payload.previous`). Propagation must not overwrite a `manual` row (§6 "unless").
- `confirm(classification_id, by)`: allowed for anyone holding `manager` role or any `ReportingLine` manager of the item's assignee (§13.2); sets `is_confirmed=true, confirmed_by`.
- Query helper `classified_as(dimension_key, value_keys[]) -> Select` used by the P6 filter compiler.

### 3.3 Process & stages
- Builtin process creation with `Stage.category` mapping: idea→ideation, discovery/design→planning, build→execution, test→validation, staging→release, production→operate, retired→closed; `maps_to_state_category`: idea..staging→`open` for idea/discovery/design and `active` for build/test/staging; production→`done`; retired→`done`. (Document this choice in ADR‑0005; the spec requires the field but not the default mapping.)
- `place(target, process, stage, *, moved_by, reason, source)`:
  1. Close current placement (`exited_at=now`) if any for that process.
  2. If `StageTransitionRule`s exist for (from→to) check `allowed_roles` (P4 enforces; P3 records) and `required_evidence_kinds` (count `Evidence` of those kinds on the item; if missing, still place but create a `ReviewItem{kind=missing_evidence}` — capture‑style non‑blocking for reporters; for overlay actors return `409` with the missing kinds, since managers may be blocked).
  3. If `stage.requires_approval` → create `Approval{target=stage_placement, policy from Project.settings.approval_policy or any_one, requested_by}`; `placement.approval_id` set.
  4. Insert new placement; mirror `Classification{stage}`; emit `stage.moved`.
  5. **Stage ↔ state sync (§13.1)**: if `stage.maps_to_state_category == done` and the item's state category ≠ done → set the workflow's terminal done state, `closed_at`, emit `item.state_changed{source=propagation, cause=stage}`; if moving *from* a done‑category stage to a non‑done one → reopen symmetrically. Intermediate stages never touch state. The reverse hook (state → stage) is registered on the P1 extension point `on_state_category_changed`: entering `done` moves the placement to the process's first `category=closed` stage; leaving `done` moves it to the stage recorded in `Event.payload.previous_stage` of the last sync or the first `execution` stage. Guard against ping‑pong with a per‑transaction `sync_depth` flag.
  6. `auto_when` on rules: evaluated by the P8 engine (store only in P3).
- `wip_count(stage)` = current placements whose item state category ≠ done; `GET /processes/{id}/board` returns stage columns with items (feeds P6 pipeline layout).
- Stage age = `now - current.entered_at`; expose `stage_age_days` on item reads.

### 3.4 Products, objectives, links
- `Product` tree with `parent_product_id`, cycle check; `ProductLink` CRUD with uniqueness `(product_id, target_type, target_id)`; a project may link to multiple products but exactly one with `role=delivers` (service rule; ADR if contested).
- Items inherit `product` classification from their project's `delivers` product (propagation).
- `Objective`/`KeyResult` CRUD; `Classification{dimension=objective}` is how items/projects link (§6).

### 3.5 Weights
- `WeightScheme.formula` grammar (JSON): `{"base": "estimate_hours"|"points"|"constant", "constant": 1.0, "multipliers": {"priority.<value>": n, "stage.<key>": n, "tag.<name>": n, "type.<key>": n, "impact.<1-5>": n, "dimension.<key>.<value>": n}, "clamp": [min,max]}`. Evaluate: `base × Π applicable multipliers`, clamp. Missing estimate → base 1.0 (so weight still exists; the "no estimate" policy catches it).
- Default scheme per workspace: `{"base":"constant","multipliers":{"priority.high":2,"priority.highest":2.5,"priority.critical":3}}` → reproduces the seed's `weight` column.
- `Weight` rows recomputed by job `weights.recompute(item_id)` after any event that can change inputs (`estimate.set`, `item.priority_changed`, `stage.moved`, `tag.linked`, `classification.changed`). `WorkItem.weight` mirrors the workspace **default** scheme only; views/metrics pick schemes explicitly (§13.4).
- Manual override: `PUT /items/{key}/weights/{scheme}` → `source=manual, set_by`; recompute skips manual rows.
- `value_score = impact × weight / max(estimate_hours, 1)` when `impact` set, else null.

### 3.6 Propagation engine (`domain/overlay/propagation.py`)
Trigger table (event → rule):

| event | rule |
|---|---|
| `product.priority_changed`, `product.risk_linked` | to linked projects (`ProductLink.role=delivers`) → their items, unless `item.priority_source=manual` |
| `project.priority_changed`, `project.visibility_changed`, `project.process_changed` | to items; item override wins (`priority_source=manual`, `visibility != inherit`, explicit placement in another process) |
| `item.priority_changed`, `stage.moved`, `item.team_changed` (parent) | subtree via `path <@`; child override wins |
| `risk.linked` (to project/product/parent) | `RiskLink{inherited=true}` on all items under it; unlink removes inherited links only |
| `project_member.added`, `product_link.added(team)` | read access on items (P4 implements the grant; P3 emits the event) |
| `project.created`, `item.created`, `item.project_changed` | inherited classifications project → product → objective |

Rules write `Event{source=propagation, correlation_id = triggering event id}` and set `priority_source=inherited` / `Classification.source=inherited` with `inherited_from_*`. Idempotent: re‑running produces no new events (compare before write).

Roll‑ups (computed on read for subtree size ≤ 500, else by job into `work_item_rollups(item_id, progress_pct, remaining, sum_weight, open_risk_score, stage_distribution jsonb, hours_logged, updated_at)`): progress %, remaining estimate, Σ weight, open risk score, stage distribution, hours logged.

### 3.7 Risks & approvals
- `Risk` CRUD per project; `target_type/target_id` allows attaching to product or item; `RiskLink` explicit links; `RiskAction` with optional `work_item_id` (create a task from an action: `POST /risks/{id}/actions/{aid}:create-item`).
- Approvals: `POST /approvals/{id}/decisions` by an approver (project `manager|owner` or named approvers in `Project.settings.approvers`); resolution per policy; on approve → `approval.resolved` → placement/milestone becomes effective; on reject → placement is rolled back to the previous stage (new placement row, `reason="approval rejected"`).

### 3.8 Review queue API
- `GET /review-queue` returns `ReviewItem`s + unconfirmed classifications, scoped: items in projects where the actor is `ProjectMember{owner|manager}` **or** whose assignee reports to the actor (`ReportingLine`), de‑duplicated so a row appears for exactly one manager — the assignee's primary `line` manager if they are also a project manager of that project, else the project owner (UX §3.2 "same item never appears twice"; ADR‑0006 for the tie rule).
- Row kinds (UX §3.2 mock): `unclassified_project`, `stage_suggestion`, `provisional_tag`, `missing_estimate`, `risk_mention`, `unknown_person`, `unconfirmed_classification`, `missing_evidence`, `low_confidence_match`.
- Actions: `POST /review-queue/{id}:resolve {choice}` → performs the write (classify/place/merge tag/set estimate/link risk/assign) and writes glossary learning: `(raw phrase → chosen value)` into `glossary_entries{source=learned, project_id}`.
- Bulk: `POST /review-queue:bulk {ids[], action}`; `:confirm-all-suggested`.

### 3.9 Bulk edit with preview
- `POST /items:bulk-preview {filter|ids, set:{priority|impact|weight|stage|…}}` → `{affected: n, children_affected: m, overrides_kept: k, sample[]}`; `POST /items:bulk` applies, one event per item, same `correlation_id`.

### 3.10 Collision handling (UX §3.4)
- Optimistic concurrency: overlay `PATCH` requires `If-Match: <updated_at or version>`; mismatch → `409` with current value and diff. Same‑field edits by two managers → last write wins only if the client re‑reads; both get a notification event `item.field_conflict` (consumed by P5 UI as the one‑click revert).

### 3.11 Plan ↔ execution linkage
- Deliverables are the intended parents of reporter tasks. Resolution order when a captured item lands in a project that has a plan: LLM hint `parent` (if ≥ 0.7) → `translator_rules`/glossary mapping phrase → deliverable → fuzzy title match of the task against deliverable titles (`pg_trgm ≥ 0.5`) → else leave `parent_id` null and create `ReviewItem{kind=unlinked_task, suggestion: top‑3 deliverables with scores}`; the LLM gateway's `/v1/suggest/review` refines the suggestion. The review queue row offers `[Deliverable X ✓] [other…] [keep unlinked]`.
- Roll‑ups (§3.6) run on the plan tree: deliverable `progress_pct` from children (`progress_mode=children` by default for `phase`/`deliverable` types), hours logged, remaining estimate, open risk score; milestone `deliverables_done/total`; project health. With zero tasks, roll‑ups are null and statuses are `not_started`, never errors.
- Moving a deliverable's dates re‑evaluates dependent deliverables (`finish_to_start` + lag) and marks violations of the chain as `at_risk`; a P8 template `plan_slip` (deliverable planned_end moved later than baseline by > N days) is pre‑registered here as a `ReviewItem` kind until the engine exists.
- The P1 `/projects/{key}/plan` read includes, per deliverable, `linked_tasks: n, hours_logged, last_activity_at` so plan‑only and active projects share one shape.

## Data & API contracts (new routes, all under `/api/v1/w/{ws}`)
`/dimensions`, `/dimensions/{id}/values`, `/classifications`, `/processes`, `/processes/{id}/stages`, `/processes/{id}/board`, `/items/{key}/stage` (PUT), `/items/{key}/placements` (history), `/products`, `/products/{id}/links`, `/objectives`, `/objectives/{id}/key-results`, `/weight-schemes`, `/items/{key}/weights`, `/risks`, `/risks/{id}/links|actions`, `/approvals`, `/approvals/{id}/decisions`, `/review-queue`, `/items:bulk-preview`, `/items:bulk`, `/items/{key}/provenance`, `/items/{key}/rollup`.

Events to register: `dimension.*`, `classification.changed|confirmed`, `stage.moved`, `approval.requested|decided|resolved`, `product.*`, `product_link.*`, `objective.*`, `key_result.updated`, `weight.recomputed|overridden`, `risk.*`, `risk.linked|unlinked`, `review.resolved`, `item.priority_changed|impact_changed|visibility_changed|project_changed|team_changed|reopened|field_conflict`.

## Acceptance criteria
- [ ] Builtin dimensions exist for the seed workspace and `Classification` rows mirror every item's project/stage/team/priority (count check: items × 4 ≥ rows).
- [ ] Moving `WEB-101` to `production` sets state to done, `closed_at`, and emits two events (`stage.moved{source=overlay}`, `item.state_changed{source=propagation}`); moving the state back to `doing` moves the stage to `build` (last non‑closed) with `source=propagation`; no infinite loop.
- [ ] Exclusion constraint: concurrent `place` calls (asyncio gather ×10) leave exactly one current placement.
- [ ] Linking `DATA-R3` to epic `DATA-80` gives every child item an inherited `RiskLink`; unlinking removes only inherited links; a manually linked child keeps its link (UX §7.2).
- [ ] Setting priority `critical` on project `GOV` propagates to items except one whose `priority_source=manual`; events carry `correlation_id` of the trigger; re‑running propagation is a no‑op.
- [ ] Default weight scheme reproduces the seed's weights for all 42 items; a second scheme `effort-only` (`base=estimate_hours`) yields different `Weight` rows while `WorkItem.weight` is unchanged.
- [ ] Approval on `staging` entry: placement has `approval_id`, pending approval shows in board as pending; reject rolls back with a new placement row.
- [ ] Review queue for `@mahmoud` (owner of GOV? per seed, platform lead) shows `GOV` items' unconfirmed rows and none of squad‑b's unless he is their line manager; the same row never appears for two managers (property test over random reporting graphs).
- [ ] Resolving `unclassified_project` for raw phrase "the ingestion retry" → `DATA` creates a learned glossary entry that the P2 pack then contains.
- [ ] Capture pipeline: a new item in DATA lands on `discovery` (project default) with product `DPLAT` inherited and a default weight, all in the same `correlation_id`.
- [ ] Bulk preview numbers equal the bulk apply results.
- [ ] Seed project `ERP` (zero tasks) returns a full plan tree with statuses `not_started`, milestones `planned|due_soon`, null roll‑ups, no errors in any list/board/pipeline endpoint; a captured task "configure the GL module" in `ERP` is auto‑parented under deliverable "General ledger configuration" (fuzzy) or lands in the review queue with it as the top suggestion.
- [ ] Capture‑vs‑overlay column guard still passes; coverage ≥ 80 % for `domain/overlay`.

## Exit checklist
- [ ] Seed goes through services end‑to‑end and still yields the snapshot counts.
- [ ] ContextPack now includes `stages`, `risks`, `process`, `default_stage`, `required_dimensions`.
- [ ] ADR‑0005 stage→state category defaults; ADR‑0006 review‑queue ownership tie rule; ADR‑0007 weight formula grammar.
- [ ] `docs/api/propagation.md` with the trigger table and a worked example.

## Handover notes
P4 will wrap every read with visibility filters and every overlay write with permission checks; keep service signatures `(actor, …)` so the checks can be added in one decorator. P6 builds its filter compiler on `classified_as()` and `StagePlacement{current}`.
