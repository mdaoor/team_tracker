# Access control

How Team Tracker decides who may see and do what (Phase 4). The implementation lives in `apps/server/tt_server/acl/`; routers
stay thin and only declare `needs("<permission>")` and call `visible(select, actor.acl, Model)`.

## Two primitives

* **`needs(permission, item=… | project=…)`** (`api/authz.py`) -- a route dependency. It resolves the object (visibility first:
  an invisible object is `404`), then asks `AclContext.can(permission, obj)`; a visible object the actor may not change is
  `403` with `details.permission`. See ADR-0008.
* **`visible(select, ctx, Model)`** (`acl/visibility.py`) -- appends one `WHERE` predicate per entity: `WorkItem`, `Project`,
  `Product`, `Activity`, `TimeEntry`, `Evidence`, `DailyLog`, `Event`, `ContextNote`, `Risk`, `Violation`, `Habit`,
  `SavedView`, `Insight`, `MetricSnapshot`. Lists, counts and cursors are computed after the predicate.

`AclContext` (`acl/context.py`) is loaded once per request: role assignments, project membership, team ancestry, the
reporting-line closure, active access grants, and the token scopes. `can()` is true when any of these holds, in order:

1. the actor holds `workspace_admin` (`*`);
2. a role assignment grants the permission at workspace scope, or at a project / team / product scope that contains the object
   (item -> project -> product, team -> parent teams);
3. the actor is the project owner or a project manager (`ProjectMember.role`) -- the `project_owner` set, scoped to that project;
4. an own-row permission applies (`item.write_capture`, `time.write`, ... on rows the actor reported / is assigned);
5. an unexpired `AccessGrant` on the object or an ancestor whose level covers the permission (`read` < `comment` < `write` <
   `admin`; `inherit_to_children` controls descent).

Everything else is denied. Denials are logged through structlog (`acl_denied` with actor, permission, project / item) and
counted by the `tt_acl_denied` OpenTelemetry counter; they are not domain events.

## Builtin roles

| role | scopable | permissions |
|---|---|---|
| `workspace_admin` | workspace only | `*` |
| `manager` | yes | `approval.decide`, `capture.write`, `classification.confirm`, `classification.write`, `comment.write`, `context_note.write`, `dailylog.review`, `evidence.write`, `habit.coach`, `habit.view_team`, `item.read`, `item.write_capture`, `item.write_overlay`, `objective.write`, `policy.read`, `project.create`, `review_queue.resolve`, `risk.write`, `time.write`, `translator_rule.write`, `view.save`, `view.share`, `violation.resolve`, `violation.waive`, `weight.set` |
| `project_owner` | yes | `approval.decide`, `approval.request`, `capture.write`, `classification.confirm`, `classification.write`, `comment.write`, `context_note.write`, `dailylog.review`, `evidence.write`, `habit.coach`, `habit.view_team`, `item.read`, `item.write_capture`, `item.write_overlay`, `iteration.write`, `milestone.write`, `objective.write`, `policy.read`, `project.create`, `project.write`, `project_member.write`, `review_queue.resolve`, `risk.write`, `time.write`, `translator_rule.write`, `view.save`, `view.share`, `violation.resolve`, `violation.waive`, `weight.set` |
| `contributor` | yes | `capture.write`, `comment.write`, `evidence.write`, `item.read`, `item.write_capture`, `time.write`, `view.save` |
| `viewer` | yes | `item.read`, `view.read` |
| `feature_admin:dimensions` | workspace only | `dimension.admin` |
| `feature_admin:process` | workspace only | `process.admin` |
| `feature_admin:products` | workspace only | `product.admin` |
| `policy_admin` | workspace only | `policy.admin`, `policy.read` |

`workspace_admin` is created for the workspace creator; every new member gets `contributor`. Custom roles are per workspace
(`Role.workspace_id`), built from the same permission names. Role assignment itself needs `role.admin`.

## Visibility order for work items

1. **Inbox projects** (`kind=inbox`): visible only to the owner and the owner's `line|senior` managers. Not to admins, not to
   teammates, and never in team / product / workspace listings (`GET /items?team=` skips them).
2. **Own rows**: reporter, assignee or an open `Assignment`, plus the people those persons manage.
3. **Item-level grant** on the item or an ancestor (`path <@`).
4. Otherwise the **effective tier**, which is the item's own `visibility` unless it is `inherit`, then the project's:
   `private` / `assignees` (own rows only -- membership and system grants do **not** widen them), `members` (project members),
   `team` (the project team subtree), `project` (anyone with `item.read` on the project), `workspace` (every member).
   On top of the tier, workspace admins always see the item, and managers whose scope covers the project (workspace-wide
   `manager`, or `manager` / `project_owner` at that project or its team) see every tier except `private` / `assignees`.

Projects use the same tiers without the own-row step. Products are visible when `workspace`, owned, granted, or to admins and
workspace managers.

## Reporting lines

`managers_of` / `reports_of` (`acl/reporting.py`) honour `valid_from/to` and `scope`. A `line|senior` manager reads their
reports' items, activities, daily logs, habits and violations in every category. `project|functional|mentor|dotted` managers
read only inside the scope of the line. Review-queue ownership uses the same resolver (ADR-0006).

## Wellbeing and capture privacy

* `Habit` / `Violation` with `category=wellbeing`: the subject and `line|senior` managers only -- never project managers, never
  workspace admins, regardless of grants. Other categories also reach managers whose scope covers the subject
  (`habit.view_team`). A subject always sees their own habits.
* `Event.raw_input`, `Activity.note` / `mood` / `client_meta` are readable by the actor and their `line|senior` managers; others
  get the row with those fields `null` (`redact_event`, `redact_activity`, and `acl.visibility.redact(ctx, kind, row)` for the
  snapshot / export assemblers of Phase 7).

## Project membership and grants

`project_member.added` creates a **system** `read` grant on the project (`granted_by` NULL, inherits to children);
removal soft-deletes system grants only. Linking a team to a product does the same for the team. Manual grants
(`POST /grants`) survive membership changes and may carry `expires_at`; expired grants stop working immediately.

## Token scopes

API tokens carry cumulative scopes, narrowing their owner's rights (never widening them):

| scope | allows |
|---|---|
| `capture` | `/capture/*`, `/context-pack`, `GET /activities?person=me`, daily-log reads and confirm |
| `read` | + every `GET` |
| `manage` | + every write the owner may do, except administering people: `/role-assignments`, `/grants`, `/idp/*`, `/members`, `/reporting-lines`, `/teams` |
| `admin` | everything the owner may do |

A scope failure is `403 insufficient_scope`. Browser sessions (cookie) are not narrowed.

## Exports

CSV and print output is built with `acl/export.py`: `csv_export(handle, scope, header, rows)` writes
`# Exported by @sara · scope project WEB · at 2026-10-23T09:30:00Z` as the first and last line and neutralises formula
injection; `print_footer` supplies the same text for `@media print`. `is_stamped()` is what endpoint tests assert with. The
endpoints themselves arrive in Phases 6 and 7.

## Scope selector

`GET /me/scopes` lists the workspace, products, projects, teams and people that contain anything the actor can read, for the
scope picker in the web console.

## Tests

`tests/unit/test_acl_matrix.py` generates the allow/deny matrix from the catalogue (roles x permissions x object relationship);
`tests/api/test_acl_*.py` cover each router (a guard test fails when a router module has none), privacy, tokens, identity and a
10 k-item performance check (p95 about 65 ms for a member and 75 ms for an admin on the dev database against the 150 ms budget).
