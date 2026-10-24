# Phase 8 — Governance engine: CEL policies, templates, violations, habits, metrics

## Goal
Implement object model §8 end‑to‑end on the server: the three‑tier CEL design (templates → CEL → registered functions), `on_event` and `scheduled` evaluation, `Violation` lifecycle, nightly `Habit` computation from built‑in detectors, `MetricSnapshot` per scope/day/scheme, and `PolicyTestRun` dry‑runs. The seven example policies in §8 ship as built‑in templates and reproduce the seed's violations.

## Depends on
Phase 6 (`tt_filter` for `candidate_filter`), Phase 4 (ACL, `managers_of` for notify), Phase 3 (placements, weights).

## Inputs
- Object model §8 (all), §13.5, §14
- `gen_snapshot.py` violation and habit rules (acceptance oracle)

## Scope
1. `packages/cel_policies` (`tt_cel`): CEL environment (`celpy`), **policy context schema v1** (`item, activity, person, project, stage, placement, event, metrics, now`), type declarations for static checking, Tier‑3 function registry, template compiler, cost/length limits.
2. Built‑in `PolicyTemplate`s (≥ 10) with parameters and CEL sources; workspace templates; `published_scope=shared` copy semantics (§14.2).
3. `Policy` CRUD with validation (type‑check CEL against the context schema at save), activation, `grace_period`, `notify`, `auto_action`.
4. Evaluation: `on_event` fan‑out from the outbox; `scheduled` via cron rows → Procrastinate periodic tasks → `candidate_filter` → batch CEL; de‑duplication (one open `Violation` per (policy, target)); grace period; auto‑actions.
5. `Violation` lifecycle API: acknowledge/resolve/waive/false_positive with reason; auto‑resolve when the condition clears on re‑evaluation.
6. `MetricSnapshot` job: per scope (person/team/project/product/stage/workspace) per day per weight scheme, the metric list in §8 "Performance & impact".
7. `HabitDefinition`s (the 11 built‑ins in §8 table) + nightly `Habit` computation with window/trend/score, linked violations, status workflow, coaching note.
8. Notifications: `Policy.notify {roles[], reporting_line[], channel}` → `NotificationPort` (log + in‑app `notifications` table; Slack in P11).
9. `KeyResult.source=metric_snapshot` updater (P3 stub).
10. `StageTransitionRule.auto_when` evaluation (CEL over the same context) → auto stage moves (`source=rule`).

## Out of scope
UI (P9). LLM anomaly insights beyond the P7 rule stubs.

## Detailed tasks

### 8.1 Context schema v1 (`tt_cel/context/v1.py`)
Build a `ContextBuilder` that, for a target + optional event, loads one object graph and exposes it to CEL as maps with declared types:

| root | fields (selection) |
|---|---|
| `item` | id, key, title, project{key,methodology}, type{key}, state{key,category}, priority, priority_source, weight, impact, estimate_value, estimate_unit, remaining_value, planned_start/end, due_date, actual_start/end, assignee{handle,team}, team{slug}, tags[], risks[{key,score,status}], stage{key,category,expected_duration_days,wip_limit}, placement{entered_at,exited_at}, classification{<dim_key>: value_key or list}, is_provisional, created_at, closed_at, **functions**: `evidence_last_at()`, `evidence_count(kind?)`, `hours_logged(window)`, `reopen_count()`, `days_in_stage()`, `children_count()`, `open_violations(category?)` |
| `activity` | id, kind, status, started_at, ended_at, duration, person, item |
| `person` | id, handle, team, capacity_hours_per_week, time_zone, **functions**: `metrics.minutes_logged(person.id, window)`, `metrics.active_items(person.id)`, `metrics.distinct_items(person.id, "today")` |
| `project` | key, methodology, status, planned_end, team, product, **functions**: `metrics.wip(project.id, stage?)`, `metrics.overrun_rate(project.id, window)` |
| `stage` | key, category, expected_duration_days, wip_limit, **functions**: `wip_count()` |
| `placement` | entered_at, exited_at, process, stage |
| `event` | type, source, occurred_at, payload |
| `daily_log` | date, status, minutes_total, item_ids |
| `metrics` | namespace of Tier‑3 functions reading `metric_snapshots` / indexed queries |
| `now` | timestamp; `business_days(n)` duration helper; `duration("3d")` |

Rules: declared with `celpy` type environment so `cel-python`'s checker catches `item.wieght`; functions are Python callables registered in `tt_cel/functions/*.py` with a docstring that becomes the template‑editor help; **no function may issue more than one SQL statement without an index** (review checklist). Version the schema (`context_schema_version=1`); a policy saved against v1 is evaluated with v1 adapters even after v2 ships.

### 8.2 Built‑in templates (`tt_cel/templates/builtin/*.yaml`)
Each: `key, name, category, severity_default, target, trigger, params[{name,type,default,min,max}], cel_source, candidate_filter_template, description, example`. Ship at least:

| key | CEL (abridged) | target/trigger |
|---|---|---|
| `stuck_in_stage` | `placement.exited_at == null && item.days_in_stage() > {days \|\| stage.expected_duration_days}` | stage_placement / scheduled hourly |
| `no_estimate_weighted` | `event.type == "activity.started" && item.estimate_value == null && item.weight > {min_weight}` | activity / on_event |
| `evidence_gap` | `item.state.category == "active" && item.evidence_last_at() < now - business_days({days})` | work_item / scheduled daily |
| `overrun` | `event.type == "item.finished" && item.actual_end > timestamp(item.planned_end)` | work_item / on_event |
| `overwork` | `metrics.minutes_logged(person.id, "today") > {minutes}` | person / scheduled daily |
| `unclassified_in_stage` | `item.stage.key == "{stage}" && !has(item.classification.{dimension})` | work_item / on_event+scheduled |
| `wip_breach` | `stage.wip_limit != null && stage.wip_count() > stage.wip_limit` | stage / on_event(stage.moved) |
| `missing_required_dimension` | from `Dimension.is_required_for` | work_item / on_event |
| `stale_inbox_item` | `item.is_provisional && now - item.created_at > duration("{days}d")` | work_item / scheduled |
| `long_session` | `activity.status == "active" && now - activity.started_at > duration("{hours}h")` | activity / scheduled hourly |
| `late_daily_log` | `daily_log.status == "draft" && now - timestamp(daily_log.date) > duration("36h")` | daily_log / scheduled |

Seed workspace gets `stuck_in_stage`, `no_estimate_weighted`, `evidence_gap`, `overrun`, `wip_breach(GOV/build=4)` activated so the acceptance oracle applies.

### 8.3 Policy service
- Save: compile template + params → CEL (or take raw CEL for `policy.admin`), **type‑check** against the context schema, enforce length ≤ 4 KB and `celpy` cost cap; store `condition_cel`, `template_id`, `template_params`, `candidate_filter` (validated by `tt_filter`), `context_schema_version`.
- Dry‑run: `POST /policies/{id}:test {window: "30d"}` → replays over historical state where possible (for `scheduled`: current rows filtered by the window; for `on_event`: events in the window with reconstructed context from `Event.payload` + current row — document the approximation) → `PolicyTestRun{fired_count, sample_target_ids}`.
- Activation requires a `PolicyTestRun` newer than the last edit (UI nudges; API enforces with `?force=1` override for admins).

### 8.4 Evaluation runtime
- `on_event`: outbox handler `governance.on_event(event_id)` selects active policies whose `event_types` contain the type (or prefix match `item.*`) and whose scope contains the entity; builds context once per entity; evaluates each; emits `violation.opened|updated|auto_resolved`.
- `scheduled`: Procrastinate periodic task per policy (cron from `Policy.schedule`); `candidate_filter` compiled via `tt_filter` within `scope`; evaluate in batches of 500 with a shared `metrics` cache per run; auto‑resolve open violations whose target no longer matches (`status=resolved, resolution_note="condition cleared"`).
- Grace period: first match stores `violations{status=open, detected_at}` only if the condition has held since `first_matched_at ≥ grace_period` (track in `policy_match_state(policy_id, target_id, first_matched_at)`).
- De‑dup: unique partial index `(policy_id, target_type, target_id) WHERE status IN ('open','acknowledged')`.
- Accountability: `Violation.person_id` = item assignee / activity person / daily‑log person / stage → null; denormalise `team_id, project_id, product_id`.
- `auto_action`: `{"set": {"priority": "high"}}` → overlay write with `source=rule`; `{"create_item": {...}}` → item in the policy owner's project; `{"notify": …}`; `{"move_stage": "..."}`; all audited.
- Notifications: resolve `notify.roles` within scope and `notify.reporting_line` via `managers_of(person, kinds)`; de‑dup per (violation, recipient) per day.

### 8.5 Metric snapshots (`jobs/metrics.py`, nightly + on demand)
Per `scope_type in (person, team, project, product, stage, workspace)` × `date` × `weight_scheme_id`, `metrics` JSON: `throughput_weighted, value_delivered, hours_logged, cycle_time_p50{stage}, lead_time_p50, wip, blocked_count, overrun_rate, estimate_accuracy, violation_count{category}, habit_scores{key}, risk_exposure`. Use SQL window functions; store also `inputs{item_ids_done[]}` for drill‑down. `KeyResult{source=metric_snapshot, metric_key}` updated from the scope's latest snapshot.

### 8.6 Habits (`jobs/habits.py`, nightly)
- Load `HabitDefinition`s (built‑ins seeded from §8 table; `formula` JSON: `{"numerator": "<metric or violation policy_key>", "denominator": "<opportunity metric>", "min_opportunities": 3}`; `thresholds: {"flag_rate": 0.3, "windows": ["30d","90d"]}`).
- For each subject (person, team, project) × window: `occurrences`, `rate = occurrences / opportunities`, `trend` vs previous window (±10 % band → stable), `score` = clamp(rate × polarity‑adjusted weight), `linked_violation_ids` (for violation‑based habits), `first/last_seen_at`; upsert `Habit` keeping manager‑owned `status`/`coaching_note`; `status=flagged` automatically when rate ≥ `flag_rate` and status was `observed`; wellbeing‑category habits (`long_sessions`, `overwork`‑derived) set `category=wellbeing`.
- Detectors mapping (from §8 table): `late_finish` (overrun violations / done items), `estimate_drift` (|est−actual|/est over done items with estimates), `no_estimates` (no_estimate_weighted / started weighted items), `evidence_gap` (days active without evidence / active days), `context_switching` (days with > N distinct items or sessions < 15 min / working days), `long_sessions` (activities > 4 h / activities), `log_consistency` (+, confirmed daily logs / working days), `early_delivery` (+, done before planned / done), `stage_stalls` (stuck_in_stage / placements), `unassigned_inbox_age` (provisional items > N days / items), `rework` (items re‑entering an earlier stage / items).

### 8.7 Tests
- Oracle: run the seeded workspace through the engine at `as_of` → violations equal `gen_snapshot.py`'s (policy, target) set exactly (36 rows incl. `wip_breach GOV/build`); habits equal the generator's keys per subject where deterministic (`no_estimates`, `late_finish`, `evidence_consistency` → map to `evidence_gap` positive inverse? **Decision:** the generator's `evidence_consistency` is the positive form of `evidence_gap`; implement `HabitDefinition{key=evidence_consistency, polarity=positive}` and record in ADR‑0012).
- CEL safety: fuzz expressions (Hypothesis) → never exceeds the cost cap; malformed → 400 with position; type errors → 400 naming the field.
- Idempotency: evaluating twice opens no duplicate violations; clearing the condition auto‑resolves.
- Grace period and de‑dup tests; auto‑action audit test; notify resolution test with reporting lines.
- Performance: scheduled `stuck_in_stage` over 50 k placements < 30 s; `on_event` evaluation p95 < 20 ms excluding IO.

## Data & API contracts
`/policy-templates` (list builtin+workspace, copy shared), `/policies` CRUD, `/policies/{id}:test`, `/policies/{id}:activate`, `/violations` (list with filter grammar keys), `/violations/{id}:acknowledge|resolve|waive|false-positive`, `/habits` (scoped, ACL), `/habits/{id}` (status, coaching_note), `/habit-definitions`, `/metric-snapshots?scope=&from=&to=&scheme=`, `/notifications/mine`.
Events: `policy.*`, `violation.opened|acknowledged|resolved|waived|false_positive|auto_resolved`, `habit.updated|flagged|status_changed`, `metrics.snapshot_written`, `notification.sent`.

## Acceptance criteria
- [ ] Oracle test passes (violations and deterministic habits match the generator for the seed).
- [ ] All 7 CEL examples from object model §8 compile and type‑check against context v1 unchanged.
- [ ] A policy with a typo (`item.wieght`) is rejected at save with the field name.
- [ ] `scheduled` policy respects `candidate_filter` (EXPLAIN shows the filter pushed to SQL) and `scope`.
- [ ] Shared template copied into another workspace is independent of later edits to the source (§14.2).
- [ ] Wellbeing habit invisible to a project manager, visible to line manager and subject (ACL test reused from P4).
- [ ] `KeyResult` with `source=metric_snapshot` updates after the nightly job.
- [ ] `auto_when` on a transition rule moves an item to `test` when a `pr` evidence with `merged` in payload arrives (`source=rule` event).
- [ ] Snapshot (P7) now shows violations/habits/now identical to the generator for the seed.

## Exit checklist
- [ ] `docs/governance/context-schema-v1.md` generated from the declarations (fields + functions + docstrings).
- [ ] `docs/governance/templates.md` listing built‑ins with parameters and examples.
- [ ] ADR‑0012 habit naming/polarity; ADR‑0013 dry‑run approximation for `on_event` policies.
