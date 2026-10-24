# Team Tracker — Object Model (v0.4)

Scope: data model only. One canonical store fed by lightweight clients, enriched server-side by managers, rendered as many views.

Changes from v0.2: explicit **two-layer split** (capture vs. management overlay); generic **delivery process + stages** as a cross-project dimension; **Product / Objective** grouping; generic **Dimension / Classification** system; **weights & impact**; **Policy → Violation → Habit** governance engine.

---

## 0. The two layers

| | Capture layer (reporter) | Management overlay (PMs, leads, admins) |
|---|---|---|
| who writes | any person, any client | many managers, independently, server-side |
| what | `doing 1,2,3 · started · ended · took X · plan to end at …` | project, product, stage, priority, weight, risk, people, dimensions |
| tables | `Activity`, `Evidence`, `TimeEntry`, bare `WorkItem` (title only) | everything else |
| rule | never blocks, never asks for context | attaches to capture rows by reference; never edits them |

A reporter's message creates a `WorkItem` with just `title` + a `Activity`. Later, PM-A links it to a product, PM-B puts it on stage "Testing", a lead sets weight 3 and adds a risk. All of that lands in `Classification` / `StagePlacement` / `Risk` rows that *point at* the item — the capture rows are never rewritten. Every view then shows the item in all dimensions at once.

---

## 1. Design principles

1. **One universal node `WorkItem`**, nested by `parent_id`, typed by `WorkItemType`.
2. **Append-only `Event` log** is the source of truth; tables are projections.
3. **Methodology is configuration**, any state → any state is allowed.
4. **Capture never blocks**: missing project → Inbox; unknown names → `is_provisional`.
5. **Dimensions are first-class and pluggable.** Project is just one dimension. Process stage, product, objective, team, client, cost centre, skill — all are `Dimension`s with `DimensionValue`s; anything can be classified into any of them (§4).
6. **Delivery process is a generic, cross-project pipeline** (idea → … → production). It is a `Process` of ordered `Stage`s; items, projects, and products sit on a stage via `StagePlacement`. Boards can be drawn per project *or* per process (§5).
7. **Propagation**: priority, risk, weight, people, and visibility set at product / project / parent level flow down to children unless overridden; rollups flow up (§6).
8. **Privacy from day one** (§10).
9. **Behaviour is observable**: policies produce violations; recurring violations/behaviours become habits that reports surface (§8).

---

## 2. Entity overview

```
Person (global) ──< WorkspaceMember ─── Workspace
                                        ├── Team, Membership, ReportingLine
                                        ├── Tag, CustomFieldDefinition, Role, SavedView, ContextNote
                                        ├── Dimension ──< DimensionValue          (§4)
                                        ├── Process ──< Stage ──< StageTransitionRule (§5)
                                        ├── Product ──< ProductLink (projects, items, objectives)
                                        ├── Objective (OKR / goal) ──< KeyResult
                                        ├── WeightScheme                          (§6)
                                        ├── Policy ──< Violation ──< Habit         (§8)
                                        └── Project (nestable; one Inbox per member)
                                             ├── ProjectMember, Workflow ──< WorkflowState, WorkItemType
                                             ├── Iteration, Milestone, Component, Release
                                             ├── Risk ──< RiskAction, RiskLink
                                             ├── Approval ──< ApprovalDecision
                                             ├── RecurrenceRule
                                             └── WorkItem (tree)
                                                  ├── Classification   (item ↔ DimensionValue)
                                                  ├── StagePlacement   (item ↔ Stage, with history)
                                                  ├── WorkItemLink, TagLink, Assignment, Estimate, Weight
                                                  ├── Checklist, Comment, Attachment, CustomFieldValue, Baseline
                                                  ├── Activity ──< Evidence         ← CAPTURE
                                                  └── TimeEntry                    ← CAPTURE
Person ──< DailyLog                                                               ← CAPTURE (auto)
Access: AccessGrant, RoleAssignment      Log: Event      Derived: MetricSnapshot, Insight
```

---

## 3. Tenancy, people, hierarchy (unchanged from v0.2, condensed)

- **`Person`** (global): `id, display_name, email, kind (human|bot|external), time_zone, is_active`
- **`Workspace`**: `id, name, slug, settings json`
- **`WorkspaceMember`**: `id, workspace_id, person_id, handle, capacity_hours_per_week?, cost_rate?, status, inbox_project_id`
- **`Team`**: `id, workspace_id, parent_team_id?, name, slug, kind (org|virtual|ad_hoc), lead_id?, valid_from/to?`
- **`Membership`**: `team_id, person_id, role (lead|member|viewer), allocation_pct?, joined_at, left_at?`
- **`ReportingLine`**: `person_id, manager_id, kind (line|senior|project|functional|mentor|dotted), scope_type (none|project|team|product), scope_id?, is_primary, valid_from/to?`
- **`ContextNote`**: `scope_type (workspace|project|team|person|product|process), scope_id, author_id, kind (goal|okr|guideline|background|decision|link|onboarding|other), title, body, pinned, visibility, valid_from/to?`

---

## 4. Dimensions — the generic classification system

Project is one dimension; managers can add any other without schema changes.

### `Dimension`
| field | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| key | str | `project`, `product`, `stage`, `objective`, `team`, `client`, `cost_centre`, `platform`, `skill`, `quarter`, … |
| name | str | |
| kind | `builtin \| custom` | builtin dimensions are backed by real tables (Project, Product, Stage, Team, Objective); custom ones are just `DimensionValue` rows |
| value_type | `tree \| flat` | tree dimensions support roll-up (product → sub-product) |
| cardinality | `single \| multi` | can an item have more than one value? |
| applies_to | `work_item \| project \| product \| activity \| person`[] | |
| is_required_for | json? | e.g. `{"work_item": {"when": "state.category == 'active'"}}` — a policy can flag missing values |
| owner_role | str? | who may edit values (`manager`, `feature_admin:dimensions`) |

### `DimensionValue`
| field | type | notes |
|---|---|---|
| id, dimension_id | uuid | |
| parent_value_id | uuid? | tree |
| key, name, color? | | |
| backing_type / backing_id | str? / uuid? | for builtin dims: `project`/`<Project.id>`, `stage`/`<Stage.id>`, … so one query path serves both builtin and custom |
| position | int | |
| is_active | bool | |

### `Classification`  ← the glue row
Links any target to a dimension value. **This is what managers write.**

| field | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| target_type | `work_item \| project \| product \| activity \| person \| risk` | |
| target_id | uuid | |
| dimension_id | uuid | |
| value_id | uuid → DimensionValue | |
| source | `manual \| inherited \| rule \| import` | inherited = propagated from parent/project/product |
| inherited_from_type / inherited_from_id | | provenance when `inherited` |
| set_by, set_at | | |
| confidence | decimal? | for rule/LLM-suggested classifications pending confirmation |
| is_confirmed | bool | |

`WorkItem.project_id` stays as a real FK (performance, keys), and the server mirrors it into a `Classification{dimension=project}` row so every dimension is queried the same way.

---

## 5. Delivery process — generic cross-project stages

A `Process` is an ordered pipeline that is independent of any project. "Software delivery": idea → discovery → design → build → test → staging → production → retired. "Content": brief → draft → review → publish. A workspace can have several; a project declares which it uses by default; an item can override.

### `Process`
| field | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| key, name, description | | |
| applies_to | `work_item \| project \| product`[] | can place a whole project on "Discovery" |
| is_default | bool | |
| owner_id | uuid | |

### `Stage`
| field | type | notes |
|---|---|---|
| id, process_id | uuid | |
| key, name, color | | `idea, discovery, design, build, test, staging, production, retired` |
| position | int | |
| category | `ideation \| planning \| execution \| validation \| release \| operate \| closed` | cross-process reporting bucket (like `WorkflowState.category`) |
| entry_criteria | json? | checklist shown on entry; a policy can enforce it |
| exit_criteria | json? | |
| expected_duration_days | int? | feeds "stuck" violations |
| wip_limit | int? | |
| requires_approval | bool | creates an `Approval` on entry |
| maps_to_state_category | `open \| active \| done`? | optional hint for syncing with workflow state |

### `StagePlacement`  ← where something is on the pipeline, with full history
| field | type | notes |
|---|---|---|
| id | uuid | |
| target_type / target_id | `work_item \| project \| product` | |
| process_id, stage_id | uuid | |
| entered_at | timestamp | |
| exited_at | timestamp? | null = current |
| moved_by | uuid | |
| reason | text? | |
| approval_id | uuid? | |

Current stage = row with `exited_at IS NULL`. Stage-cycle-time and "how long in Test" come straight from this table. `Classification{dimension=stage}` is mirrored from the current placement so stage joins the generic query path.

### `StageTransitionRule` (optional)
`process_id, from_stage_id?, to_stage_id, allowed_roles str[], required_evidence_kinds str[]?, auto_when json?` — e.g. auto-move to `test` when a linked PR is merged. Any transition allowed if no rule exists.

**Relation to workflow states:** `WorkflowState` is the project-local status (to-do / doing / done inside a sprint). `Stage` is the cross-project lifecycle. They coexist: a story can be `in_progress` (state) while the feature it belongs to is in `build` (stage). Boards can render either axis; the "generic agile board" the request asks for is simply a board with columns = `Stage`.

---

## 6. Products, objectives, weights, propagation

### `Product`
Groups projects, items, teams, and objectives toward one deliverable outcome. Tree-capable (platform → product → feature-area).

| field | type |
|---|---|
| id, workspace_id | uuid |
| parent_product_id | uuid? |
| key, name, description | |
| owner_id | uuid |
| status | `concept \| active \| sunset \| retired` |
| default_process_id | uuid? |
| visibility | enum |

### `ProductLink`
`product_id, target_type (project | work_item | team | objective | release), target_id, role (delivers | supports | depends_on | owns), weight?`

### `Objective` / `KeyResult`
`Objective`: `id, workspace_id, scope_type (workspace|product|team|person), scope_id, title, period (2026-Q4), owner_id, status`
`KeyResult`: `id, objective_id, title, metric_key?, target_value, current_value, unit, source (manual | metric_snapshot)`
Items/projects link to objectives via `Classification{dimension=objective}`.

### Priority, weight, impact on `WorkItem` (added fields)
| field | type | notes |
|---|---|---|
| priority | enum | as before |
| priority_source | `manual \| inherited \| rule` | |
| weight | decimal | effort/value multiplier used in performance math (default 1.0) |
| weight_scheme_id | uuid? | which scheme produced it |
| impact | int 1–5? | business impact, manager-set |
| value_score | decimal? | derived: `impact × weight / estimate` or scheme formula |

### `WeightScheme`
Lets different managers weight differently without fighting over one number.

`id, workspace_id, name, scope_type (workspace|product|project|team), scope_id?, formula json` — e.g. `{"base": "estimate_hours", "multipliers": {"priority.critical": 2, "stage.production": 1.5, "tag.customer_facing": 1.3}}`. The server computes `Weight` rows per scheme.

### `Weight` (computed + manual overrides)
`id, work_item_id, scheme_id, value, source (formula | manual), set_by?, computed_at`

### Propagation rules (server-side, recorded as `Event{source: propagation}`)
| set on | flows down to | unless |
|---|---|---|
| Product.priority / risk | linked projects → their items | child has `priority_source=manual` |
| Project.priority / visibility / process | items | item override |
| Parent item priority / stage / assignee team | subtree | child override |
| Risk linked to project | shows on all items (`RiskLink{inherited}`) | — |
| People added to Product/Project (`ProjectMember`) | read access on items | item `visibility=private` |

Roll-ups (up the tree, computed): progress %, remaining estimate, Σ weight, open risk score, stage distribution, hours logged.

---

## 7. Projects, work items, capture layer

### Project / Workflow / Iteration / Milestone / Component / Release / Risk / Approval / RecurrenceRule
Unchanged from v0.2, with two additions:
- `Project.default_process_id → Process`
- `Risk.target_type (project | work_item | product)` so risks attach at any level; `RiskLink` carries `inherited` flag.

### `WorkItem` — full field list
| field | type | layer |
|---|---|---|
| id, project_id, key, type_id, parent_id, path | | system |
| **title** | str | **capture** (only required field) |
| description | text? | overlay |
| state_id, priority, priority_source, severity | | overlay |
| reporter_id | uuid | capture |
| assignee_id, team_id | | overlay (defaults to reporter / reporter's team) |
| iteration_id, milestone_id, release_id, component_id | | overlay |
| **planned_end** | date? | **capture** ("plan to end at …") or overlay |
| planned_start, due_date | date? | overlay |
| **actual_start / actual_end** | timestamp? | **capture** (derived from Activity) |
| estimate_value / unit, remaining_value | | capture (`est: 3h`) or overlay |
| weight, weight_scheme_id, impact, value_score | | overlay |
| progress_pct, progress_mode | | either |
| rank, is_provisional, is_template, template_item_id | | system |
| visibility, source | | overlay |
| resolved_at, closed_at | | system |

### Capture tables (reporter-facing; never edited by overlay)
- **`Activity`**: `id, person_id, work_item_id, project_id, kind, status (active|paused|ended), started_at, ended_at?, note?, client_meta?, mood?`
- **`Evidence`**: `id, activity_id, work_item_id, person_id, kind (note|screenshot|link|commit|pr|document|file|metric), title?, body?, url?, attachment_id?, captured_at, source (manual|client_auto|integration)`
- **`TimeEntry`**: `id, person_id, work_item_id, activity_id?, date, minutes, is_billable, note?, source`
- **`DailyLog`**: `id, workspace_id, person_id, date, summary, item_ids[], minutes_total, blockers?, status (draft|confirmed|reviewed), reviewed_by?, reviewed_at?`

### Other item tables (unchanged)
`WorkItemLink`, `Assignment`, `Tag` / `TagLink`, `Estimate`, `Checklist` / `ChecklistItem`, `Comment`, `Attachment`, `CustomFieldDefinition` / `CustomFieldValue`, `Baseline` — as in v0.2.

### `Event` — canonical log
As v0.2, plus `source (client | overlay | propagation | rule | import)` so capture events and management events are distinguishable in audits.

---

## 8. Governance — policies, violations, habits

Purpose: monitor team performance and impact, surface violations, and detect habits (good and bad) to work on.

### `Policy`
A declarative rule evaluated on events, on a schedule, or both.

| field | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| key, name, description | | |
| scope_type / scope_id | `workspace \| product \| project \| team \| process` | |
| target | `work_item \| activity \| person \| project \| stage_placement \| daily_log` | |
| trigger | `on_event \| scheduled` | |
| event_types | str[]? | for `on_event` |
| schedule | cron? | for `scheduled` |
| condition | json | predicate over target + joins (see examples) |
| severity | `info \| warning \| violation \| critical` | |
| category | `process \| quality \| time \| planning \| collaboration \| compliance \| wellbeing` | |
| notify | json | `{roles:[…], reporting_line:["line"], channel:"…"}` |
| auto_action | json? | e.g. `{"set": {"priority":"high"}}`, `{"create_item": …}` |
| grace_period | duration? | |
| is_active | bool | |
| owner_id | uuid | |

Example conditions (JSON predicate grammar, AND by default):
```json
// stuck in a stage
{"target":"stage_placement","where":{"exited_at":null,"days_in_stage":{"gt":"stage.expected_duration_days"}}}
// activity started with no estimate on a weighted item
{"target":"activity","on":"started","where":{"item.estimate_value":null,"item.weight":{"gt":1}}}
// no evidence for 3 working days on an active item
{"target":"work_item","where":{"state.category":"active","evidence.last_at":{"older_than":"3bd"}}}
// overrun: actual > planned_end
{"target":"work_item","on":"finished","where":{"actual_end":{"gt":"planned_end"}}}
// overwork
{"target":"person","schedule":"daily","where":{"time_entries.minutes_today":{"gt":600}}}
// unclassified work in production stage
{"target":"work_item","where":{"stage.key":"production","classification.product":null}}
// WIP limit breach
{"target":"stage_placement","where":{"stage.wip_count":{"gt":"stage.wip_limit"}}}
```

### Policy authoring — options, trade-offs, recommendation

The `condition` field needs a language. Five realistic choices:

| option | what it is | pros | cons |
|---|---|---|---|
| **A. Custom JSON predicate DSL** (the examples above) | Your own schema: `{"field":{"op":value}}` with a few path helpers | Trivial to validate with JSON Schema; easy to build a form UI on top; no dependency; safe by construction | You will keep extending it (arithmetic, OR/NOT, date math, aggregates) until it is a badly-designed expression language; no reuse across languages; hard to read once nested |
| **B. CEL (Common Expression Language)** | Google's sandboxed, non-Turing-complete expression language; used by Kubernetes, Firebase rules, Envoy. Runtimes for Go, Java, Python (`cel-python`), JS (`cel-js`), Rust | Real expressions (`&&`, `||`, arithmetic, duration/timestamp math, list/map ops, macros like `exists()`); statically type-checked against a declared schema before saving; guaranteed termination, bounded cost; readable one-liners; same string evaluates identically in server and in a browser preview | Not a query language — it evaluates against one in-memory object, so scheduled policies over thousands of items need a candidate pre-filter; custom aggregates must be exposed as registered functions; managers will need a snippet library, not a blank box |
| **C. JSON-Logic / json-rules-engine** | Off-the-shelf JSON rule formats with evaluators in most languages | Zero design work; serialisable; UI builders exist | Lisp-in-JSON readability (`{"and":[{">":[…]}]}`); weak typing; no date/duration semantics; mediocre error messages; ecosystems are small |
| **D. SQL (policy = a SQL query that returns violations)** | Store a `SELECT … FROM work_items …` per policy; run on schedule | Unlimited expressive power incl. aggregates and windows; pushes down to the DB; analysts already know it | Not safe to expose to managers (injection, full scans, locks); cannot run per-event cheaply; couples policies to physical schema, so every migration breaks rules; hard to preview or unit-test |
| **E. Scripting sandbox (Python/JS/Lua)** | Policies are small functions in a sandboxed runtime | Anything is expressible, including LLM calls | Sandboxing is a security project of its own; non-terminating or expensive scripts; versioning and review burden; overkill for "is X older than 3 days" |

Rego/OPA was considered and rejected: excellent for authorisation, awkward for time-series/behavioural rules, and a second language for managers to learn.

**Recommendation: a three-tier design built on CEL.**

1. **Tier 1 — Templates (what 90 % of managers use).** A `PolicyTemplate` table of parameterised, pre-tested rules: "Item stuck in {stage} longer than {days}", "No evidence for {n} working days", "Finished after planned end by more than {hours}", "WIP in {stage} above {limit}", "Daily logged time above {minutes}". Authoring is a form; the template compiles to CEL. Non-technical, safe, consistent naming across workspaces.
2. **Tier 2 — CEL expressions (power users, `policy.admin`).** Free-form CEL against a declared, versioned **policy context schema** (`item`, `activity`, `person`, `stage`, `metrics`, `now`). Type-checked on save; a "dry-run against last 30 days" button shows what would have fired. Templates are themselves CEL, so the two tiers share one evaluator.
3. **Tier 3 — Registered functions (engineers).** Heavy aggregates (`metrics.hours_logged(person, "7d")`, `item.evidence_last_at()`, `stage.wip_count()`) are implemented once in server code, indexed/pre-computed from `MetricSnapshot`, and exposed to CEL as functions. This is how CEL stays cheap: it never scans, it reads pre-computed numbers.

Evaluation model:
- `trigger=on_event` → server builds the context for the one affected entity and evaluates; sub-millisecond.
- `trigger=scheduled` → the policy's `target` + `scope` + an optional **`candidate_filter`** (a restricted JSON filter that *does* compile to SQL — same grammar as `SavedView.filter`) narrow the set; CEL evaluates the remainder. This keeps the DB-pushdown benefit of option D without exposing SQL.

Why not pure JSON DSL (option A)? It is the right *starting point* for `candidate_filter` and for `SavedView`, where it must map to SQL anyway — but as the *rule* language it will grow into an unmaintainable CEL clone. Why not SQL? Managers cannot be given it, and per-event evaluation is impossible. CEL gives the expressiveness ceiling you need with a safety floor you don't have to build.

Schema changes:
- `Policy.condition` → `condition_cel str`, `condition_template_id uuid?`, `template_params json?`, `candidate_filter json?`, `context_schema_version int`.
- New **`PolicyTemplate`**: `id, workspace_id?, key, name, description, category, params json (name, type, default, bounds), cel_source, is_builtin`.
- New **`PolicyTestRun`**: `policy_id, window, fired_count, sample_target_ids[], ran_at` — persisted dry-runs so a change can be reviewed before activation.

Examples rewritten in CEL:
```cel
// stuck in stage
placement.exited_at == null && now - placement.entered_at > duration(string(stage.expected_duration_days) + "d")
// started with no estimate on a weighted item
event.type == "started" && item.estimate_value == null && item.weight > 1.0
// evidence gap (registered function handles business-day math)
item.state.category == "active" && item.evidence_last_at() < now - business_days(3)
// overrun
event.type == "finished" && item.actual_end > timestamp(item.planned_end)
// overwork
metrics.minutes_logged(person.id, "today") > 600
// WIP breach
stage.wip_limit != null && stage.wip_count() > stage.wip_limit
```

### `Violation`
One row each time a policy fires.

| field | type |
|---|---|
| id, workspace_id, policy_id | uuid |
| target_type / target_id | |
| person_id? | who is accountable (assignee / actor / owner) |
| team_id?, project_id?, product_id? | denormalised for rollups |
| severity, category | copied from policy |
| detected_at | timestamp |
| evidence | json (the matched values) |
| status | `open \| acknowledged \| resolved \| waived \| false_positive` |
| resolved_at?, resolved_by?, resolution_note? | |
| waived_by?, waive_reason? | managers can waive with reason |

### `Habit`
A **pattern** aggregated over time, per person or team — positive or negative. Computed nightly from `Violation` + behavioural metrics.

| field | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| subject_type / subject_id | `person \| team \| project` | |
| key | str | `late_finish`, `no_estimates`, `evidence_gap`, `context_switching`, `long_sessions`, `consistent_daily_log`, `early_delivery`, `low_rework` … |
| polarity | `positive \| negative` | |
| window | `7d \| 30d \| 90d` | |
| occurrences | int | |
| rate | decimal | occurrences / opportunities |
| trend | `improving \| stable \| worsening` | vs previous window |
| score | decimal | normalised 0–1 |
| first_seen_at, last_seen_at | | |
| linked_violation_ids | uuid[] | |
| status | `observed \| flagged \| in_coaching \| resolved` | manager-managed |
| coaching_note | text? | |
| visibility | `subject_and_managers \| managers_only` | wellbeing-sensitive habits default to managers_only |

Built-in habit detectors (each is a stored definition, `HabitDefinition{key, polarity, formula, thresholds}`):

| key | signal |
|---|---|
| `late_finish` | share of finished items with `actual_end > planned_end` |
| `estimate_drift` | mean |estimate − actual| / estimate |
| `no_estimates` | share of started weighted items without estimate |
| `evidence_gap` | days active with no Evidence |
| `context_switching` | distinct items per day / sessions < 15 min |
| `long_sessions` | activities > 4 h without pause |
| `log_consistency` (positive) | share of working days with confirmed DailyLog |
| `early_delivery` (positive) | share finished before planned_end |
| `stage_stalls` | placements exceeding expected duration |
| `unassigned_inbox_age` | provisional items older than N days |
| `rework` | items re-entering an earlier stage |

### Performance & impact (computed, stored in `MetricSnapshot`)
Per person / team / product / project / stage, per day:
`throughput_weighted (Σ weight of done), value_delivered (Σ value_score), hours_logged, cycle_time_p50 per stage, lead_time_p50, wip, blocked_count, overrun_rate, estimate_accuracy, violation_count by category, habit_scores, risk_exposure (Σ open risk score)`

### `Insight` (derived, optional storage)
LLM- or rule-generated narrative tied to evidence: `id, scope_type, scope_id, period, kind (summary | anomaly | recommendation), body, supporting_ids[] (violations, habits, metrics), generated_at, status (new | reviewed | actioned | dismissed)`.

---

## 9. Views & dimensions

### `SavedView`
As v0.2; `filter` now accepts any dimension by key:
```json
{
  "dims": {"product":"PLATFORM-X","stage":{"in":["build","test"]},"objective":"Q4-O2"},
  "people": {"assignee_in_team":"squad-b"},
  "people2": {"reports_to":"@mahmoud","kind":["line","project"]},
  "priority": {"gte":"high"},
  "weight": {"gte":2},
  "due_date": {"between":["tomorrow","tomorrow"]},
  "touched": {"since":"-14d"},
  "state": {"category":["open","active"]},
  "risk": {"open_score_gte":12},
  "violations": {"open":true,"category":["time","process"]}
}
```
`layout` adds `pipeline` (columns = stages of a `Process`) and `matrix` (two dimensions on axes, e.g. product × stage).

### Derived views
| view | reads |
|---|---|
| Now board | `Activity{active}` |
| Project board | WorkItem × WorkflowState |
| **Pipeline board (generic agile)** | StagePlacement{current} × Stage, filterable by any dimension |
| Product map | Product tree → projects → items with stage distribution |
| Gantt / variance | planned vs actual vs Baseline |
| Risk matrix | Risk p×i at project/product level with inherited links |
| Team performance | MetricSnapshot + Habit + Violation per team |
| Violations inbox | open Violations grouped by severity / policy |
| Habit report | Habit rows with trend, for 1:1s and retros |
| Manager digest | DailyLogs + Events + ContextNote + Insights in window |

---

## 10. Access control

As v0.2 (`Role`, `RoleAssignment`, `AccessGrant`, object `visibility`, reporting rule). Additions:
- Object types extended: `product, process, dimension, policy, violation, habit, objective`.
- New permissions: `dimension.admin, process.admin, product.admin, policy.admin, violation.resolve, violation.waive, habit.view_team, habit.coach, weight.set, classification.write`.
- `Habit` and `Violation` with `category=wellbeing` are visible only to the subject and their `line`/`senior` managers, never to project managers, regardless of grants.
- `classification.write` is scoped: a PM can classify items in their projects into any dimension, but cannot edit `Dimension` definitions.

---

## 11. Relationship summary (additions to v0.2)

| from | to | cardinality | via |
|---|---|---|---|
| Dimension | DimensionValue | 1 : N (tree) | fk / parent_value_id |
| DimensionValue | Project / Stage / Product / Team / Objective | 0..1 : 1 | backing_type + backing_id |
| any target | DimensionValue | N : M | Classification |
| Process | Stage | 1 : N | fk |
| any target | Stage | N : M over time | StagePlacement (history) |
| Product | Product | tree | parent_product_id |
| Product | Project / WorkItem / Team / Objective / Release | N : M | ProductLink |
| Objective | KeyResult | 1 : N | fk |
| WorkItem | Weight | 1 : N (per scheme) | fk |
| Policy | Violation | 1 : N | fk |
| Violation | Habit | N : M | linked_violation_ids |
| Person / Team | Habit | 1 : N | subject |
| Risk | Project / WorkItem / Product | N : 1 + N : M | target + RiskLink |

---

## 12. Client contract (unchanged surface, richer server)

Reporter still sends only:
```
doing: 1. fix login, 2. write tests, 3. deploy | took: 2h | plan: tomorrow 5pm
started: fix login
done: fix login
```
Server creates/updates `WorkItem{title}`, `Activity`, `TimeEntry`, `planned_end`, then runs: fuzzy match → Inbox/project placement → inherited classifications (project → product → objective) → default stage from project's process → weight computation → `on_event` policies. Managers later adjust any overlay row; every adjustment is an `Event{source: overlay}` and recomputes rollups.

---

## 13. Resolved decisions (v0.3 → v0.4)

1. **Stage ↔ state sync, both directions.** `Stage.maps_to_state_category` is now required. Moving an item into a stage with `category=closed` sets its state to the workflow's terminal `done` state and `closed_at`; setting a `done`-category state moves its placement to the process's first `closed` stage. Reverse moves reopen symmetrically. Both are recorded as `Event{source: propagation}` so the audit shows which side triggered it. Intermediate stages never touch state.
2. **Classification confirmation:** any person holding `manager` role (or any `ReportingLine` manager of the item's assignee) may set `Classification.is_confirmed=true`. `confirmed_by` is recorded.
3. **Habits are always visible to the subject** (`visibility=subject_and_managers` is the only value; column removed). Wellbeing-category habits remain hidden from project managers per §10.
4. **Weight scheme selection is per report.** `SavedView` and `MetricSnapshot` requests take `weight_scheme_id`; snapshots are stored per scheme. No implicit precedence.
5. **Policy authoring:** three-tier CEL design — see §8 "Policy authoring".

## 14. Resolved (v0.4)

1. **Tier-3 registered functions are platform-owned**, shipped with releases and versioned via `Policy.context_schema_version`. Workspaces extend behaviour through templates and CEL only. Revisit a plugin mechanism once the function catalogue stabilises.
2. **Templates:** built-in set ships with the platform; workspaces may publish their own (`PolicyTemplate.is_builtin=false`, `published_scope (workspace | shared)`), and shared templates are copied, not linked, so later edits never change another workspace's active policies.

Python reference implementation: `team_tracker_models.py`.
