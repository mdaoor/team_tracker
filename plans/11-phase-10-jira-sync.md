# Phase 10 — Jira sync (server‑side projection)

## Goal
Items in projects linked to a Jira project are projected to Jira issues by the **server** (never the client); the reporter's `jira` block on a `ClientMessage` (Mode A `--jira` / Mode B toggle, see Phase 2 §2.6) triggers the push; Jira webhooks feed back only `status`, `assignee`, `comments` as `Event{source: import}`. Jira is a projection, not a second source of truth (UX §2.5).

## Depends on
Phase 3 (fields to map), Phase 1 (capture events). Can run in parallel with P4–P9.

## Inputs
- UX §2.5 (mapping table, direction rules), §3.1 (Jira link in project setup)
- Object model: `Project.external_refs{"jira": "WEB"}`, `WorkItem.external_refs{"jira": "WEB-142"}`, `Event.source=import`

## Scope
1. Jira Cloud connector (REST v3, OAuth 2.0 3LO or API token per workspace; stored encrypted) in `integrations/jira/`.
2. Per‑project mapping config (`Project.settings.jira`): `project_key, issuetype_map{type_key→name}, priority_map, status_map{workflow_state_key→jira status / transition id}, done_status, stage_field (custom field id for "Delivery Stage"), sprint_field, auto_send (bool), sync_time_logs (bool), sync_comments (bool)`.
3. Outbound push job on events: `item.created|updated|state_changed|planned_end_set|estimate.set|tag.linked|stage.moved|time.logged|assignment.changed` when `project.settings.jira.enabled` and (`auto_send` or the originating message's `jira.send=true`, remembered on the item as `external_refs["jira_opt_in"]="true"`).
4. Inbound webhooks `POST /integrations/jira/webhook/{workspace}` (signed): `jira:issue_updated` → `status`, `assignee`, `comment_created` → `Comment{author=mapped person or external}`; loop prevention (ignore changes whose `author` is our integration user or whose `changelog` matches our last push hash).
5. Backfill/link: `POST /projects/{key}/jira:link-existing` matching by summary or explicit key; `POST /items/{key}/jira:push` manual push; `GET /items/{key}/jira` status.
6. Review queue rows for mapping gaps (`jira_mapping_gap`: unknown status/issue type/priority) and for sync failures.
7. Reporter‑visible result: capture response `jira: {status: pushed|deferred|failed, key}`; CLI card shows the Jira chip.

## Out of scope
Jira Server/DC specifics (document differences), bidirectional field sync beyond the three inbound fields, Jira as the item creation source (that would make it a second source of truth).

## Detailed tasks
- Client: `httpx` with retry/backoff, rate‑limit handling (429 `Retry-After`), idempotent create via a `tt_item_id` custom property / label `tt:<id>` so a retried create finds the existing issue.
- Mapping engine: `title→summary`, `description→description (ADF)`, `item_type→issuetype`, `priority→priority`, `assignee→assignee (accountId via email lookup, cached)`, `planned_end→duedate`, `estimate→timetracking.originalEstimate`, `tags→labels`, `iteration→sprint (Agile API)`, `stage→customfield_<id>`, `TimeEntry→worklog (one per entry; store worklog id in a `jira_worklogs` table)`, `done→transition to done_status`, reopen → transition back if a path exists else comment.
- State: `jira_links(item_id pk, issue_key, issue_id, last_pushed_hash, last_pushed_at, last_inbound_at, status)`; `WorkItem.external_refs.jira` mirrors `issue_key`.
- Push job is **per item**, coalescing bursts (debounce 5 s via job key); computes the desired issue state from current rows and diffs with `last_pushed_hash` → only changed fields are sent.
- Inbound: map Jira status → workflow state via `status_map`; when a reporter moves state in Jira, apply as `Event{source=import}` through the normal item service so stage ↔ state sync and policies run; unknown status → review row, no change.
- Security: webhook secret per workspace; verify `X-Hub-Signature`‑style HMAC (Jira Cloud: use a shared secret query token + IP allow‑list; document).
- Admin UI (small): per‑project Jira panel in Setup (P5 placed the fields) gains "Test connection", mapping tables with Jira‑fetched options, sync log (last 50 pushes with diff).
- Tests: contract tests against a recorded Jira sandbox (VCR cassettes); loop‑prevention test (webhook echo of our own push produces no event); mapping gap → review row; retry on 429.

## Acceptance criteria
- [ ] Posting a Mode A batch with `jira.send=true` for `WEB` creates a Jira issue, returns its key in the capture response, and stores `external_refs.jira`.
- [ ] `done` in Team Tracker transitions the Jira issue; changing status in Jira moves the TT state (`source=import`) and triggers the stage sync; no echo loop.
- [ ] Time entries become worklogs exactly once (idempotent under job retry).
- [ ] Projects without `auto_send` push nothing unless the reporter opted in on that item.
- [ ] Jira outage: pushes queue and retry with backoff; the capture path is never slowed (push is async).
- [ ] Mapping gaps appear in the review queue with a one‑click mapping fix.

## Exit checklist
- [ ] `docs/integrations/jira.md`: setup, mapping, direction rules, limits.
- [ ] ADR‑0015 "Jira is a projection; inbound limited to status/assignee/comments".
