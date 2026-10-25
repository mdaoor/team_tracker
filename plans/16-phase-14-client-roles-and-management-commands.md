# Phase 14 — One client for everyone: role modes and management commands from the client app

## Goal
The same client app that reporters use to send tasks (CLI, desktop palette, mobile, Slack — Phases 2 and 11) becomes the daily tool for **managers, scrum masters, coaches, product/project managers and admins** too. In a role mode, the person can do from the client everything the server web console allows them: record their own work, create projects and epics, build plans, merge tasks, create virtual teams, add tags, assign, prioritise, move stages, link risks, approve, resolve review‑queue rows, waive violations, write coaching notes. The two sending modes stay: **assisted** (free text → LLM → a command the person confirms) and **manual** (forms generated from the command schema). The web console remains the rich surface for setup and analysis; the client is for fast, frequent actions.

## Depends on
Phase 13 R6.1–R6.2 (suggestions, capture intent), Phase 3 (overlay services), Phase 4 (ACL, roles), Phase 2b (gateway), Phase 11 (shells).

## Inputs
- Product requirement (this planning round): *the client app should apply to both employee and managers; a manager could send from the same client his actions, tasks, or create projects, build epics, merge many tasks, create virtual teams, add tags… all things he could do in the server side app… in different roles like manager, scrum master, coach, PM.*
- Object model §0 (two layers), §10 (permissions), UX §2 (palette), §3.2 (review queue)
- Phase 2 §2.6 (assisted/manual modes, outbox JSON files)

## Design principles
1. **Capture never blocks; management commands validate.** Capture messages keep rule 1 (never rejected). Management commands are a different contract: they are authorised and validated server‑side and **may be rejected** with a clear reason, because they change the shared overlay. The client makes this visible: capture rows have no "Confirm" step; commands always show a preview and need one confirmation (or `--yes`).
2. **Same body, two heads.** One `ClientBatch` file in the outbox can hold capture messages and management commands; the server processes them in order within the batch, each with its own idempotency key and result.
3. **Commands map 1:1 to existing services.** No new business logic for a command that the web console already performs; the command layer is a thin, schema‑described façade over `domain/*` services, so ACL and events are identical whichever surface is used.
4. **Role mode is a lens, not a permission.** Switching the client to "Scrum master" changes which verbs and forms are offered and how the day view is organised; the server still decides with the person's real roles and grants.
5. **Destructive or wide actions get a server‑side preview.** Merge, bulk changes, reparenting with children, team membership changes show "will affect N items / M people" before confirmation (reuse `items:bulk-preview`).

## Scope
1. **Server: command contract and endpoint.** `ClientCommand{client_id, actor, at, op, args, on_behalf_of?, preview_only?}` in `tt_schema.client`; `ClientBatch.commands[]` next to `messages[]` (schema version bump, backwards compatible); `POST /api/v1/w/{ws}/commands` and acceptance of commands inside `/capture/batches`; a **command catalogue** (`GET /commands/catalogue`) that publishes every op with its JSON Schema for args, required permission, whether it supports preview, and a one‑line description — this feeds both the manual forms and the LLM translator.
2. **Server: the ops** (initial catalogue; each delegates to an existing service, adding one where missing):
   | group | ops |
   |---|---|
   | projects | `project.create {name,key,process?,product?,team?,methodology?}`, `project.update`, `project.add_member {person,role}`, `project.plan.create` (Phase 1 §1.2 payload), `project.baseline.capture` |
   | items | `item.create {title,type,project,parent?,…}` (overlay‑created, e.g. epics/deliverables), `item.update {key, fields…}`, `item.reparent {keys[], parent}`, `item.merge {sources[], target|new_title}` (**new service**: re‑points activities, time, evidence, links, tags, assignments, classifications; sources soft‑deleted with `merged_into`; events), `item.split {key, titles[]}`, `item.assign {key, person, role}`, `item.set_priority|set_impact|set_weight|set_stage|set_state|set_dates|set_estimate`, `item.link {from,to,type}`, `item.tag {keys[], add[], remove[]}` |
   | teams & people | `team.create {name, kind: org|virtual|ad_hoc, lead?, members[]}`, `team.add_members`, `team.remove_members`, `reporting_line.set {person, manager, kind, scope?}` |
   | dimensions & tags | `tag.create`, `tag.merge`, `dimension.value.add`, `classify {target, dimension, value}` |
   | process & risk | `risk.create`, `risk.link`, `risk.action.add`, `approval.decide {approval, decision, comment}`, `stage.move {keys[], stage, reason}` |
   | governance | `review.resolve {row, choice}`, `review.bulk`, `violation.ack|resolve|waive {id, reason}`, `habit.coach_note {habit, note}`, `habit.set_status`, `policy.activate|deactivate` |
   | iterations & milestones | `iteration.create|start|close`, `milestone.create|complete`, `item.move_to_iteration {keys[], iteration}` |
   | personal (any role) | all capture actions already exist as messages; `dailylog.confirm`, `context_note.add` |
   Each op declares `permission`, `preview: bool`, `destructive: bool`, `args_schema`. Server validates args against the schema, authorises via `acl.require`, runs preview or apply through the service, writes `Event{source=client}` with `payload.command=op`, returns `{client_id, status: applied|rejected|previewed, result, preview, errors[{pointer, code, message}]}`. Rejections are **not** 4xx for the batch: per‑command status, batch `207`.
3. **Server: role presets.** Builtin `Role`s added: `scrum_master` (iterations, stage moves, WIP/review, approvals request, item.update in own projects), `coach` (habit.view_team, habit.coach, context notes, no item writes), `project_manager` (= `project_owner` preset scoped per project), `admin` exists. `GET /me` returns `roles[]`, `can{}` and the **role modes** the client may offer (`reporter`, `manager`, `scrum_master`, `coach`, `pm`, `admin`), derived from roles.
4. **Gateway: translate commands.** `POST /v1/translate/command` and the agentic variant: NL → `ClientCommand[]` constrained by the catalogue's schemas (fetched from the core server, cached by ETag); the agent may call read tools to resolve keys/people/teams; never applies (the client confirms and sends). Few‑shot set per op; golden ≥ 80 lines ("merge WEB-101 and WEB-105 into one", "new epic Checkout redesign under WEB", "create virtual team payments with @sara @ali led by me", "tag all open DATA items #q4", "move everything in test older than a week to build with reason 'failed QA'").
5. **Client core (`tt_client`)**: command outbox entries (same JSON file format; `commands[]`), preview call before send, confirmation step, catalogue cache, forms generated from `args_schema` (manual mode), role‑mode state, `on_behalf_of` for managers logging for a report ("for: @sara").
6. **CLI**: `tt role [reporter|manager|scrum|coach|pm]`, `tt do "<free text>"` (assisted command), `tt cmd <op> --arg …` and `tt cmd <op> --form` (manual), `tt preview <id>`, `tt send <id> --yes`, `tt queue` (my review queue, resolve inline), `tt team now` (active work of my reports), `tt approve`, `tt waive`.
7. **Desktop/mobile/Slack**: role switch in the palette header (shows only modes the person has); verb chips per role mode; command card with **Effect preview** section ("creates project PAY · adds 3 members · inherits process sw-delivery") and a Confirm button; forms for manual mode generated from the catalogue; "My queue" panel (review rows with one‑click defaults from R6.1) and "Team now" panel; Slack: `/tt do …` and modal forms; approvals and waives as Slack message buttons.
8. **Audit & safety**: every command event carries `payload.command`, `client_version`, `mode`, `role_mode`; destructive ops require confirmation even with `--yes` unless `--force`; `on_behalf_of` recorded; rate limit per token for commands; preview never writes.

## Out of scope
Rich analytics in the client (use the web console); editing policies/CEL from the client (activate/deactivate only); custom roles editor (web console).

## Detailed tasks
- **Command registry** (`domain/commands/registry.py`): decorator `@command("item.merge", permission="item.write_overlay", preview=True, destructive=True, schema=ItemMergeArgs)`; catalogue endpoint serialises it; a test asserts every registered op has a schema, a permission, a service call, and a golden example.
- **`item.merge` service** (`domain/items/merge.py`): validates same project (or `allow_cross_project` arg with permission `project.write` on both); moves capture rows by **reference** (updates `work_item_id` on activities/time/evidence — allowed because the rows' identity and content are untouched; record in ADR‑0026 why this does not violate rule 2), merges tags/links/classifications (manual beats inherited), sums estimates (latest `revised`), sets `WorkItem.deleted_at` and `external_refs.merged_into` on sources, emits `item.merged`; Jira: comments on source issues and links to target (P10).
- **Batch processing**: `/capture/batches` executes `messages[]` then `commands[]` in order with shared `correlation_id`; results array per entry; idempotency by `client_id` for both.
- **Role modes**: `me.role_modes[]` + the verb/form mapping file `tt_client/roles.yaml` (which ops each mode surfaces first; all permitted ops remain reachable via search).
- **Forms from schema**: `tt_client/forms.py` renders questionary prompts from JSON Schema (string/enum/array/person/project/team pickers backed by the pack and the catalogue); desktop uses `react-jsonschema-form`‑style generation with the pack‑backed pickers; both produce the same `ClientCommand`.
- **Previews**: `preview_only=true` returns `{affected: n, children: m, overrides_kept: k, will_create[], will_delete[], sample[]}`; client renders it in the card.
- **ContextPack additions**: `my_role_modes`, `my_teams_with_members` (for managers), `my_projects_as_manager`, `review_queue_count`, `pending_approvals`, so the LLM and forms resolve names offline.
- **Tests**: catalogue completeness; per‑op authorisation matrix (role × op → allowed/denied) generated from the registry; merge property test (sum of minutes/evidence preserved; no capture row content changed); batch with mixed messages/commands and a rejected command does not affect the applied ones; CLI e2e for `tt do "merge WEB-101 and WEB-105"` → preview → confirm → merged; Slack approval button e2e; gateway golden.

## Data & API contracts
- `ClientCommand`, `ClientBatch.commands[]` in `tt_schema.client` (schema version 0.2; ADR‑0027).
- `POST /commands`, `GET /commands/catalogue`, `POST /items:merge` (also reachable directly), `GET /me` (role modes), `GET /review-queue?mine=true` (exists), `GET /activities?team=mine` (team now).
- Events: `command.previewed` (optional, off by default), `item.merged`, `item.split`, `team.created|members_changed`, plus the existing events each service emits.

## Acceptance criteria
- [ ] A manager, from the CLI and from the desktop palette, can in assisted mode: create a project, create an epic under it, create a virtual team with three members, tag ten items, merge two tasks, move five items to a stage with a reason, approve a pending approval, resolve a review row, waive a violation with a reason — each with a preview and one confirmation, each producing the same events the web console produces (snapshot test of event types per op).
- [ ] The same manager can log their own work with the capture verbs in the same session without switching mode; a mixed batch (2 messages + 2 commands) applies in order with one `correlation_id`.
- [ ] A reporter without roles sees no management verbs; a forged command from a capture‑scoped token is rejected with `403 scope`; a command the role does not permit is rejected with a readable reason and nothing is written.
- [ ] `item.merge` preserves total minutes, evidence count and links across the merge (property test), soft‑deletes sources, and the Jira projection reflects it when P10 is enabled.
- [ ] Manual mode forms are generated from the catalogue for every op (test iterates the catalogue) and produce schema‑valid commands offline.
- [ ] Scrum master mode surfaces iterations/stage/WIP/review verbs first; coach mode cannot write items but can write coaching notes; verified by the role matrix test.
- [ ] Gateway golden ≥ 0.85 op accuracy and ≥ 0.9 argument accuracy on the 80‑line set with the scripted model; never emits an op absent from the catalogue.
- [ ] Destructive ops cannot be sent without confirmation; `--yes` alone does not bypass; `--force` does and is logged.

## Exit checklist
- [ ] `docs/client/management-commands.md` (catalogue, role modes, examples, safety rules), ADR‑0026 (merge semantics vs rule 2), ADR‑0027 (ClientBatch 0.2), ADR‑0028 (commands may be rejected; capture may not).
- [ ] UX doc addendum `docs/spec/team_tracker_ux_addendum_roles.md` describing the role switch, command card and effect preview (mock in text like UX §2.2).
- [ ] Appendix E rows for the requirement.

## Handover notes
Phase 15 builds the personal tracker on the same client core; keep role mode, catalogue cache and command outbox in `tt_client` with no UI dependencies.
