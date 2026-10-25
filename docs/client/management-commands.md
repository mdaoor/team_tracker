# Management commands from the client (Phase 14)

The client that reporters use to send work (CLI, desktop palette, mobile, Slack) also sends **management commands**: create a
project or epic, merge tasks, create a virtual team, tag, assign, move stages, approve, resolve review rows, waive violations,
write coaching notes. A command is a thin, schema-described façade over the code the web console calls, so permissions and events
are identical on every surface.

## Capture is never rejected, commands may be
Capture messages keep rule 1 (accepted and corrected later). A command changes the shared overlay, so it is **authorised and
validated** and may be rejected with a reason. A rejection is a per-command result (`status: rejected`, `errors[]`), the batch
answers `207`, and nothing is written for the rejected command or changed for its neighbours.

## Contract
* `GET /api/v1/w/{ws}/commands/catalogue` → `{etag, ops[]}`; `ETag`/`If-None-Match` supported. Each op: `op`, `group`, `description`,
  `permission`, `preview`, `destructive`, `modes`, `args_schema` (JSON Schema), `example {args, text}`. Forms (manual mode) and the
  gateway's command translator are generated from it.
* `POST /commands` with `{workspace, commands: [ClientCommand]}` (max 50) → `{results: [...]}`; `POST /capture/batches` accepts
  `commands[]` after `messages[]` and runs both in order under one `correlation_id` (`command_results[]` in the response).
* `ClientCommand {client_id, actor, at, op, args, on_behalf_of?, preview_only?}`; `args._meta` carries
  `{client_version, mode, role_mode, confirmed?, forced?}` for the audit trail. `client_id` is the idempotency key.
* Result: `{client_id, op, status: applied|rejected|previewed, result, preview, errors[{pointer, code, message}]}`.
* `POST /items:merge` is the direct form of `item.merge`.
* `GET /me` lists `roles[]`, `role_modes[]`, `can{}`; the ContextPack carries `my_role_modes`, `my_teams_with_members`,
  `my_projects_as_manager`, `review_queue_count`, `pending_approvals`; `GET /activities?team=mine` is "team now".

## Role modes
`reporter`, `manager`, `scrum_master`, `coach`, `pm`, `admin`. A mode is a **lens**: it changes which verbs and forms the client
offers first. The server decides with the person's real roles and grants; every permitted op stays reachable by search. The modes a
person may select are derived from their roles (`GET /me`).

| role | what it adds |
|---|---|
| `scrum_master` | iterations, stage moves, WIP/review queue, approvals request, item edits in their projects |
| `coach` | habit coaching notes and status, context notes; **no item writes** |
| `project_manager` | the project-owner preset, scoped per project |

## Safety
* **Preview first.** Ops with `preview` return `{affected, children, overrides_kept, will_create[], will_delete[], sample[]}` and never write.
* **Destructive ops** (`destructive: true`, plus wide bulk actions on the client) are applied only with `_meta.confirmed` (the person
  confirmed the preview) or `_meta.forced` (`--force`, logged). `--yes` alone confirms ordinary ops only.
* **Authorisation** is declared per op and enforced before it runs; an invisible object is `not_found`, a missing right `forbidden`
  with `details.permission`.
* **Scope.** Tokens need scope `manage` or above; a `capture` token gets `403 insufficient_scope`.
* **Audit.** Events carry `source=client` and `payload.command = {op, client_id, client_version, mode, role_mode, forced?, on_behalf_of?}`.
* See ADR-0050 (merge semantics) and ADR-0056 (execution semantics).

## Catalogue
| op | group | permission | flags | what it does |
|---|---|---|---|---|
| `approval.decide` | process | any member | – | Approve or reject a pending approval |
| `classify` | tags | classification.write | – | Classify an item or project on a dimension |
| `context_note.add` | personal | context_note.write | – | Add a context note for a project, team or the workspace |
| `dailylog.confirm` | personal | any member | – | Confirm your daily log |
| `dimension.value.add` | tags | dimension.admin | – | Add a value to a dimension (customer, theme, ...) |
| `habit.coach_note` | governance | habit.coach | – | Write a coaching note on a habit (visible to managers and coaches only) |
| `habit.set_status` | governance | habit.coach | – | Set a habit's coaching status |
| `item.assign` | items | item.write_overlay | – | Assign an item to a person |
| `item.bulk_set` | items | item.write_overlay | preview, destructive | Set priority, impact, stage or weight on many items with a propagation preview |
| `item.create` | items | item.write_overlay | – | Create an item (epic, deliverable, task ...) |
| `item.link` | items | item.write_overlay | – | Link two items (blocks, depends on, relates to ...) |
| `item.merge` | items | item.write_overlay | preview, destructive | Merge items: their time, evidence, links, tags and assignments move to the target |
| `item.move_to_iteration` | iterations | iteration.write | – | Put items into an iteration |
| `item.reparent` | items | item.write_overlay | preview, destructive | Move items under another parent |
| `item.set_state` | items | item.write_overlay | – | Change an item's workflow state |
| `item.split` | items | item.write_overlay | preview | Split an item into several siblings (the original stays, linked) |
| `item.tag` | items | item.write_overlay | preview | Add or remove tags on items |
| `item.update` | items | item.write_overlay | – | Change overlay fields of an item (priority, impact, dates, assignee, team) |
| `iteration.close` | iterations | iteration.write | destructive | Close the active iteration |
| `iteration.create` | iterations | iteration.write | – | Create an iteration |
| `iteration.start` | iterations | iteration.write | – | Start a planned iteration |
| `milestone.complete` | iterations | milestone.write | – | Complete a milestone |
| `milestone.create` | iterations | milestone.write | – | Create a milestone |
| `policy.activate` | governance | policy.write | – | Activate a tested policy |
| `policy.deactivate` | governance | policy.write | – | Deactivate a policy |
| `project.add_member` | projects | project_member.write | – | Add a person to a project |
| `project.baseline.capture` | projects | project.write | – | Freeze the current plan as a named baseline |
| `project.create` | projects | project.create | preview | Create a project |
| `project.plan.create` | projects | project.write | – | Create a top-down plan (phases, deliverables, milestones) |
| `project.update` | projects | project.write | – | Rename or change a project's status |
| `reporting_line.set` | teams | reporting_line.write | – | Set who a person reports to |
| `review.bulk` | governance | any member | preview, destructive | Apply one action to many review rows |
| `review.resolve` | governance | any member | – | Resolve a review-queue row |
| `risk.action.add` | process | risk.write | – | Add a mitigation action to a risk |
| `risk.create` | process | risk.write | – | Register a risk |
| `risk.link` | process | risk.write | – | Link a risk to an item |
| `stage.move` | process | item.write_overlay | preview | Move items to a process stage |
| `tag.create` | tags | classification.write | – | Create a tag |
| `tag.merge` | tags | classification.write | destructive | Merge one tag into another |
| `team.add_members` | teams | team.write | – | Add people to a team |
| `team.create` | teams | team.write | preview | Create a team (org, virtual or ad hoc) with members |
| `team.remove_members` | teams | team.write | preview, destructive | Remove people from a team |
| `violation.ack` | governance | any member | – | Acknowledge a policy violation |
| `violation.resolve` | governance | violation.resolve | – | Mark a violation resolved |
| `violation.waive` | governance | violation.waive | – | Waive a violation with a reason |
