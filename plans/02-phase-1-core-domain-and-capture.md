# Phase 1 — Core domain and capture ingestion

## Goal
A reporter can send a `ClientBatch` and the server does everything object model §12 lists up to and including "default stage" (stage comes in P3; here we stop at project placement): create/match `WorkItem`, create `Activity`, `TimeEntry`, `Estimate`, `Evidence`, set `planned_end`, auto‑pause the previous activity, build the `DailyLog` draft, and write `Event{source: client}` rows with `correlation_id`. Managers do not exist yet as a UI, but the tenancy and project tables are fully served by CRUD APIs so the seed and tests can use them.

## Depends on
Phase 0.

## Inputs
- Object model §3, §7 (WorkItem, capture tables, Event), §12
- UX §2.3 (verbs), §2.6 (end‑of‑day), §7.1 (flow)
- `tt_schema.client` contract

## Scope
1. Tenancy: Person, Workspace, WorkspaceMember (with `inbox_project_id` provisioning), Team, Membership, ReportingLine, ContextNote — CRUD + list.
2. Projects: Project, ProjectMember, WorkItemType, Workflow/WorkflowState (with a builtin default workflow `todo/doing/done`), Iteration, Milestone, Component, Release — CRUD. `RecurrenceRule` creation + the job that materialises recurring items.
3. Items: WorkItem (full), WorkItemLink, Assignment, Tag/TagLink (with `is_provisional`), Estimate, Checklist(+Item), Comment, Attachment (metadata only; storage adapter interface with local‑disk impl), CustomFieldDefinition/Value, Baseline.
4. Capture ingestion: `POST /api/v1/capture/batches` implementing the resolution pipeline below. Activity lifecycle (start/pause/resume/done/blocked), TimeEntry, Evidence, DailyLog draft job, end‑of‑day notification hook (interface only).
5. Auth v1: personal API tokens (`POST /auth/tokens`, `Authorization: Bearer tt_…`), dev OIDC stub. Actor resolution → `WorkspaceMember`.
6. Event catalogue entries for everything above; outbox fan‑out skeleton (`items.*`, `activity.*` → no‑op handlers for now).
7. Minimal read endpoints needed by P2's ContextPack: my open items, projects with aliases, people, tags, milestones, iterations.

## Out of scope
Dimensions/classifications/stages/products/weights/risks/approvals (P3), ACL beyond "member of workspace" (P4), any UI.

## Deliverables
- Services and routers for the three bounded contexts + capture.
- `docs/api/capture-pipeline.md`: the resolution pipeline with a sequence diagram.
- Contract tests driven by the JSON Schema.

## Detailed tasks

### 1.1 Tenancy
- `WorkspaceMember` creation **always** creates the member's Inbox project: `Project{kind=inbox, key=f"INBOX-{handle}", owner_id=person, visibility=private, workflow_id=default, default_item_type_id=task}` and stores `inbox_project_id`.
- `Team` supports `parent_team_id` tree; `GET /teams/{id}/members?effective=true` includes descendants' members.
- `ReportingLine`: validate `scope_type`/`scope_id` pairing; `is_primary` at most one per person per `kind=line` (partial unique index added in migration `0002`).
- `ContextNote` CRUD with `visibility`, `pinned`, validity window; list by scope.

### 1.2 Projects
- Builtin `Workflow` "Simple" per workspace on workspace creation: states `todo(open, initial)`, `doing(active)`, `done(done, terminal)`. Builtin `WorkItemType`s per workspace: epic(0), story(1), task(2), bug(2), subtask(3), spike(2), phase(0), deliverable(1), decision(2) with `estimation_unit=hours`.
- `Project` create requires only `name, key, owner_id`; everything else defaults (workflow=Simple, default type=task). `methodology` is informational in P1.
- Key generation per architecture §4.7: table `project_key_sequences(project_id pk, next int)`.
- `Iteration` state machine `planned → active → closed`; only one `active` per project (service check). On `closed`, compute `completed_points` from items whose state category is done.
- `Milestone` `completed_at` set manually in P1 (approval gating in P3). `kind` (`gate|release|demo|deadline|other`) is first‑class in the API and the plan editor.
- **Top‑down project plan (product requirement: managers plan first, reporters may never log a task).** A project is fully plannable with zero capture rows:
  - `POST /projects/{key}/plan` accepts a nested plan `{phases: [{title, planned_start, planned_end, deliverables: [{title, planned_start, planned_end, owner?, milestone?, depends_on?: [ref]}]}], milestones: [{name, kind, due_date, description}]}` and creates `WorkItem{type=phase}` → `WorkItem{type=deliverable}` children (`reporter_id = manager`, `source={"origin": "plan"}`), `Milestone` rows, `WorkItemLink{finish_to_start, lag_days}` dependencies, and `WorkItem.milestone_id` links; all in one transaction with `plan.created` events. Individual CRUD on phases/deliverables is the normal items API (type `phase`/`deliverable`).
  - Derived **plan status** (computed on read, no column): for a deliverable `not_started | in_progress | done | overdue | at_risk` — `done` if state category done; `overdue` if `planned_end < today` and not done; `at_risk` if `planned_end` within 7 days and progress < 50 % or a predecessor is overdue; `in_progress` if any child task/activity exists; else `not_started` ("no activity reported yet"). For a milestone: `completed | overdue | due_soon(≤14d) | planned`, plus `deliverables_done/total`. For a project: `planned_start/end` default to min/max of phases if null; health = worst of its milestones.
  - `Baseline` capture (`POST /projects/{key}/baselines {name}`) is how a plan is "approved"; variance = current planned dates vs baseline.
  - `GET /projects/{key}/plan` returns the tree with statuses, dependencies, milestones, baseline variance — the single read used by the P5 plan editor and the P7 roadmap slides.
- `RecurrenceRule`: job `recurrence.materialise` runs hourly, uses `dateutil.rrule`, creates items from the template item `lead_days` ahead, sets `template_item_id`, advances `next_run_at`; idempotent per (rule, occurrence) via `external_refs["recurrence_occurrence"]`.

### 1.3 Items
- `WorkItem` create (overlay path) validates: `type_id` level vs parent level (child level must be > parent level unless same type is allowed by `settings.allow_same_level_nesting`), `state_id` belongs to project's workflow, `path` computed from parent.
- Move (`parent_id` change) rewrites `path` for the subtree in one statement (`UPDATE … SET path = new_prefix || subpath(path, nlevel(old_prefix))`).
- State change: emits `item.state_changed`; sets `actual_start` on first move to an `active` category if null; sets `resolved_at`/`closed_at` on `done` category; clears them on reopen. (Stage sync hooks arrive in P3 — leave a documented extension point `on_state_category_changed`.)
- `progress_pct` recompute job for `progress_mode in (children, checklist, time)`; `manual` untouched.
- `Assignment` history (`unassigned_at`) kept in sync with `WorkItem.assignee_id` (owner role).
- `Tag`: create‑on‑use with `is_provisional=true` when it comes from capture; `POST /tags/{id}/merge-into/{other}` re‑points `TagLink`s and soft‑deletes.
- `Estimate` rows are history; the latest `kind=original` sets `WorkItem.estimate_value/unit`, latest `remaining` sets `remaining_value`.
- `Baseline` capture endpoint for a project: snapshots `planned_start/end/estimate` of all items under a `baseline_name`.
- `Attachment`: `StorageAdapter` protocol (`put(bytes) -> url`, `get`) with `LocalDiskStorage`; S3 in P12.

### 1.4 Capture ingestion pipeline (`domain/capture/ingest.py`)
Input `ClientBatch{workspace, messages[], client_version}`. For each message, in order, **one transaction per message**, all sharing `correlation_id = batch_id`:

1. **Idempotency**: if an `Event` with `client_id = message.client_id` exists → return the stored result (`200`, `deduplicated: true`). Never reprocess.
2. **Actor**: resolve `message.actor` (`@handle`) to `WorkspaceMember`; if `on_behalf_of` is set, the actor must have a `ReportingLine` to that person or be in the same team, else record the message under the actor and add `warnings: ["on_behalf_of_ignored"]`. Never reject.
3. **Project**: `message.project` → match `Project.key` (case‑insensitive) → `Project.name` → `settings.aliases[]` → `glossary` (P2 ContextPack lives server‑side too) → else actor's Inbox project. Record `resolution.project.method`.
4. **Item**: if `item.id` → load; elif `item.key` → load by key (any project the actor can see); elif `item.title`:
   - candidates = actor's items with state category ≠ done in the resolved project (or across all projects if project came from Inbox), plus items where actor has an `Assignment`.
   - score = `similarity(title, candidate.title)` via `pg_trgm` (`word_similarity` for partial phrases) combined with recency decay (`last touched within 7d → +0.1`).
   - choose best if `score ≥ 0.6` and the gap to the second best is ≥ 0.1; otherwise **create** `WorkItem{title, project_id, type=project.default_item_type, state=workflow.initial, reporter_id=actor, assignee_id=actor, team_id=actor's primary team, is_provisional=(project is inbox), source={"client": client_version, "raw": raw_input}}`.
   - For `action=create` always create.
   - Store `resolution.item = {method: matched|created, score}`.
5. **Action**:
   | action | effect |
   |---|---|
   | `start` | pause any `Activity{person, status=active}` (emit `activity.paused`, reason `auto_switch`), create `Activity{kind=working, status=active, started_at=at}`, set `WorkItem.actual_start` if null, move state to the first `active` category state if currently `open` |
   | `pause` | end? no — `status=paused` on the active activity for that item (or the person's single active one if no item) |
   | `resume` | `status=active` |
   | `done` | end active/paused activity (`ended_at=at`), move item state to workflow terminal `done` state, set `actual_end=at`, `resolved_at`, `closed_at` |
   | `blocked` | `Activity{kind=blocked}` (ends the working one), `note` = reason; emits `item.blocked` |
   | `note` | `Evidence{kind=note, body=note}` on the current/most recent activity (create a zero‑length `Activity{kind=other}` if none) |
   | `evidence` | one `Evidence` per entry in `message.evidence[]`; `kind` from entry, url/body |
   | `estimate` | `Estimate{kind=original if none exists else revised}`; sync to item |
   | `plan` | `WorkItem.planned_end = planned_end.date()` (capture field) |
   | `log_time` | `TimeEntry{date=at.date() in person tz, minutes, activity_id=current}` |
   | `create` | item created in step 4; apply any other fields present |
   | `update` | apply any present capture‑layer fields; overlay hints go to step 6 |
6. **Overlay hints from the LLM** (`parent, item_type, priority, milestone, iteration, stage, product, risks, assignees, custom_fields, tags`): applied **only** when `llm_confidence[field] ≥ 0.7` (default when absent: treat as 1.0 if the field is non‑null — the client is trusted to have applied the threshold). Each applied hint writes the overlay row (P1: `tags`, `priority`, `assignees`, `parent`, `item_type`, `milestone`, `iteration`, `custom_fields`; P3 adds `stage`, `product`, `risks`) with `source=rule` / `Classification.is_confirmed=false`. Anything that cannot be resolved by exact match is **dropped into a `ReviewItem`** (table `review_items`: `workspace_id, kind, target_type, target_id, suggestion jsonb, confidence, status open|resolved, created_at`) — this table is the backing store for the P5 review queue. Create it in P1 migration `0003`.
7. **Time fields**: `minutes` → `TimeEntry`; `planned_end` → item; `estimate` → `Estimate`.
8. **Events**: `item.created|item.matched`, `activity.started|paused|resumed|ended|blocked`, `time.logged`, `evidence.added`, `estimate.set`, `item.planned_end_set`, with `source=client`, `raw_input`, `client_id`, `correlation_id`, `occurred_at=message.at`, `received_at=now`.
9. **Response** per message: `{client_id, status: applied|deduplicated|applied_with_warnings, item: {id,key,title,project_key}, activity_id?, warnings[], review_item_ids[]}`.

Failure policy: a DB error on one message does not fail the batch; that message returns `status: failed, error_code` and the batch returns `207 Multi-Status`. Only malformed JSON (schema violation) returns `422` for the whole batch, and the error body names the offending message index — this is the **only** case where capture is allowed to reject, because a client that cannot produce valid JSON is broken, not a reporter who omitted context.

### 1.5 DailyLog
- Job `dailylog.build` runs at 17:30 person‑local (configurable in `Workspace.settings.daily_log_time`) and on demand: for each member with any `Activity`/`TimeEntry`/`Event{source=client}` that day, upsert `DailyLog{status=draft, item_ids, minutes_total, summary=auto}`. Auto summary v1 = bullet list "`KEY title — Xh`"; LLM summary is P7.
- `POST /daily-logs/{id}/confirm` → `confirmed`; `POST …/review` by a manager → `reviewed, reviewed_by`.
- `NotificationPort` interface with `log_only` implementation; Slack/desktop in P11.

### 1.6 Auth v1
- `api_tokens(id, person_id, workspace_id, name, hash, last_used_at, expires_at)`; `POST /auth/tokens` (requires an existing session — in dev, `X-Debug-Actor: @handle` with `TT_DEV_AUTH=1`).
- Dependency `CurrentActor` → `(person, workspace_member)`; every router under `/api/v1/w/{workspace_slug}/…` resolves the workspace from the path and asserts membership. (Appendix A fixes the URL shape.)

### 1.7 Read endpoints for ContextPack (used by P2)
- `GET /items?assignee=me&state_category=open,active&fields=key,title,project_key`
- `GET /projects?fields=key,name,aliases,external_refs`
- `GET /people`, `GET /tags`, `GET /milestones?upcoming=90d`, `GET /iterations?state=active`

## Data & API contracts
- `POST /api/v1/w/{ws}/capture/batches` body = `ClientBatch` (exact schema from `tt_schema.client`), response as in 1.4 step 9.
- `GET /api/v1/w/{ws}/items/{key}` returns `WorkItem` + embedded latest `Activity`, `Estimate`, counts (`evidence_count`, `hours_logged`).
- Events: see appendix A list; this phase must register `item.*`, `activity.*`, `time.logged`, `evidence.added`, `estimate.set`, `dailylog.*`, `project.*`, `team.*`, `member.*`.

## Acceptance criteria
- [ ] UX §7.1: posting `{"actor":"@mahmoud","action":"start","item":{"title":"picking up the retry refactor on the pipeline"},"project":"the pipeline","estimate":{"value":2,"unit":"days"}}` with seed data (and a glossary entry `"the pipeline" → DATA`) matches an existing DATA item when one has a similar title, else creates one in DATA, starts an activity, pauses the previous one, and writes 4 events sharing one `correlation_id`.
- [ ] Posting the same batch twice returns `deduplicated: true` and creates no new rows (property test: idempotency under random batches).
- [ ] A message with unknown project, unknown `@person` in `assignees`, and no estimate is accepted: item lands in the actor's Inbox, a `ReviewItem` is created for the unknown person, no error.
- [ ] `done` on an item sets `actual_end`, terminal state, ends the activity; `start` again reopens (state → active, `closed_at=null`) and emits `item.reopened`.
- [ ] `log_time` across midnight in the person's zone lands on the correct local date.
- [ ] Fuzzy match: "fix login redirect" matches `fix the login redirect loop`; "fix checkout" does not match it (creates new).
- [ ] Capture services cannot update overlay columns and overlay services cannot update capture columns (static test from architecture §4.2).
- [ ] DailyLog job produces one draft per active member per day; confirm/review transitions emit events.
- [ ] 100 % of emitted event types are in the catalogue (test).
- [ ] Coverage ≥ 85 % for `domain/capture`, ≥ 75 % elsewhere.

## Exit checklist
- [ ] `docs/api/capture-pipeline.md` written with the table in 1.4.
- [ ] Seed still passes (it should now call `capture` and `projects` services instead of raw inserts for items — do this conversion now if time allows, otherwise keep the P3 TODO).
- [ ] OpenAPI regenerated; web client types regenerated.
- [ ] ADR‑0002 "Fuzzy match thresholds and tie policy".

## Handover notes for Phase 2
P2 needs: the ContextPack endpoint (built in P2 from the 1.7 reads), `client_message_json_schema()`, and the `/capture/batches` endpoint. The glossary store (`workspace.settings.glossary` in P1 → proper table in P2) and `manager_rules` live server‑side; the client only reads them.
