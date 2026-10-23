# Phase 4 — Access control, visibility and privacy

## Goal
Every read returns only what the caller may see; every write checks a named permission; wellbeing data obeys the reporting‑line rule; Inbox items never leak; exports are stamped. Implemented once, applied everywhere through two primitives: `require(permission, scope)` and `visible(select, actor)`.

## Depends on
Phase 3 (needs products/dimensions/process objects to exist as grant targets).

## Inputs
- Object model §10, §3 (`ReportingLine`), §8 (`Habit`/`Violation` wellbeing rule), §13.3
- UX §6, §3.2 scoping, §4 ("URLs respect ACL")

## Scope
1. `Role` (builtin + custom), `RoleAssignment` (scoped), `AccessGrant` (object‑level), permission catalogue.
2. Visibility resolver for every object type with a `visibility` column (`WorkItem`, `Project`, `Product`, `ContextNote`) including `inherit` chains and the propagation rule "people added to Product/Project get read on items unless item is private".
3. Reporting‑line derived access: a `line|senior` manager of X can read X's items, activities, daily logs, habits (all categories); `project|functional|mentor|dotted` managers can read X's items in the scope of the line only.
4. Wellbeing rule: `Habit`/`Violation` with `category=wellbeing` visible only to subject + `line`/`senior` managers, never to project managers regardless of grants (§10). Habits always visible to their subject (§13.3).
5. Capture privacy: `Event.raw_input` and `Activity.note/mood` readable by actor + line/senior managers only.
6. Inbox rule: items in `Project{kind=inbox}` are visible only to the owner (and line manager) and are excluded from team/product/workspace views until moved (UX §6).
7. Auth v2: OIDC login for the web (`/auth/login`, `/auth/callback`), session cookie, CSRF; CLI token minting via browser (`/auth/cli`); token scopes (`capture`, `read`, `manage`, `admin`).
7b. **Enterprise identity & transport (product requirement)**: Active Directory sign‑in over LDAPS (NT accounts), AD **groups → roles/teams** mapping with nightly sync, optional Kerberos SSO; **HTTPS everywhere from PEM cert/key on Linux** with internal‑CA trust for all clients (§4.6).
8. Scope selector support: `GET /me/scopes` lists workspace/product/project/team/person scopes the actor can read (UX §6 first bullet).
9. Export stamping: all CSV/print endpoints add `Exported by @handle · scope … · at …` (UX §6 last bullet) — the endpoints themselves come in P6/P7; P4 provides the helper and a test harness.
10. Audit: permission denials are logged as `Event{event_type="acl.denied", source=client|overlay}`? **No** — denials are not domain events; log them via structlog with actor/permission/object, and add a counter metric.

## Permission catalogue (`acl/permissions.py`)
Builtin roles → permissions (object model §10 list plus the obvious CRUD):

| role | permissions |
|---|---|
| `workspace_admin` | `*` |
| `manager` | `item.read, item.write_overlay, classification.write, classification.confirm, weight.set, risk.write, approval.decide, review_queue.resolve, violation.resolve, violation.waive, habit.view_team, habit.coach, view.share, context_note.write, policy.read` |
| `project_owner` (per project) | manager set scoped to the project + `project.write, project_member.write, milestone.write, iteration.write, approval.request` |
| `contributor` | `item.read (scoped), item.write_capture (own), comment.write, evidence.write, time.write (own), view.save` |
| `viewer` | `item.read (scoped), view.read` |
| `feature_admin:dimensions` | `dimension.admin` |
| `feature_admin:process` | `process.admin` |
| `feature_admin:products` | `product.admin` |
| `policy_admin` | `policy.admin, policy.read` |

`RoleAssignment.scope_type ∈ {workspace, project, team, product}`; checks resolve the object's ancestry (item → project → product; team → parent teams).

## Detailed tasks

### 4.1 Primitives
- `acl.require(actor, permission, *, obj=None, scope=None)` → raises `Forbidden(permission, obj)`; evaluated as: workspace admin → allow; role assignment granting it at workspace or at a scope containing `obj` → allow; `AccessGrant{subject in (person, person's teams, person's roles), object in ancestry(obj), level ≥ needed}` → allow; else deny. Cached per request.
- `acl.visible(select, actor, entity)` → appends a `WHERE EXISTS(...)` built from a per‑entity `VisibilityPolicy` class. Entities: `WorkItem`, `Project`, `Product`, `Team`, `Person` (profile), `Activity`, `TimeEntry`, `Evidence`, `DailyLog`, `Event`, `ContextNote`, `Risk`, `Violation`, `Habit`, `SavedView`, `Insight`, `MetricSnapshot`.
- `WorkItem` visibility resolution order: `visibility != inherit` → use it; else project's; `private` → reporter/assignee/assignments/line managers; `assignees` → + all `Assignment`s; `members` → project members (+ propagation grants); `team` → project team subtree; `project` → anyone with `item.read` on project; `workspace` → all members; `scope_members` → members of any dimension‑scoped grant; `managers_only` → roles `manager`+ only. Inbox projects force `private`.
- Decorator `@authorized("item.write_overlay", obj=lambda kw: kw["item"])` for service methods; routers never check permissions directly.

### 4.2 Reporting‑line resolver
- `managers_of(person, kinds=None, scope=None, at=today)` and `reports_of(manager, kinds, scope)` honouring `valid_from/to` and `scope_type/scope_id`; cached per request; used by visibility, the review queue (P3 → refactor to use this), notifications (P8).

### 4.3 Wellbeing & capture privacy
- `VisibilityPolicy[Habit]` and `[Violation]`: if `category == wellbeing` → subject or `line|senior` manager; else normal rules + `habit.view_team` for team scope.
- `Event` read API redacts `raw_input` unless actor is the event actor or their line/senior manager; `Activity.note`, `Activity.mood` same.
- Snapshot/export assemblers (P7) must call `acl.redact(obj, actor)`; provide it now with tests.

### 4.4 Propagation grants
- Handle P3 events `project_member.added`, `product_link.added(team)` → create `AccessGrant{subject=person/team, object=project/product, level=read, inherit_to_children=true, granted_by=system}`; removal deletes system grants only.

### 4.5 Auth v2
- OIDC via `authlib`; `Person` matched by verified email; first login to a workspace requires an invitation (`WorkspaceMember{status=invited}`) or an admin‑enabled domain allow‑list.
- Sessions: `itsdangerous` signed cookie, 12 h, sliding; CSRF double‑submit for non‑GET from the web origin; API tokens exempt.
- Token scopes enforced in `CurrentActor`: a `capture`‑only token can call `/capture/*`, `/context-pack`, `/activities?person=me`, `/daily-logs/mine` and nothing else.

### 4.6 Enterprise identity: Windows NT / Active Directory accounts and groups, HTTPS with PEM (product requirement)
Requirement: the server runs on Linux over **HTTPS using a PEM certificate**, and users sign in with their **Windows NT (Active Directory) accounts**, with **NT groups** driving access.

**Identity providers (choose per workspace; both can coexist):**
- **LDAPS to Active Directory** (`integrations/ldap/`, `ldap3`): service‑account bind; user lookup by `sAMAccountName` or `userPrincipalName`; password verification by simple bind as the user over LDAPS (636) or StartTLS; CA for the domain controllers supplied as a **PEM bundle** (`TT_LDAP_CA_BUNDLE`); attributes mapped to `Person` (`displayName → display_name`, `mail → email`, `sAMAccountName → handle` default); `memberOf` (with nested group expansion via `LDAP_MATCHING_RULE_IN_CHAIN`) read at login and refreshed by a nightly sync job.
- **OIDC** (Entra ID / ADFS / any provider) as in 4.5, with the `groups` claim used the same way as `memberOf`.
- **Kerberos / SPNEGO SSO** (optional, `TT_KERBEROS_KEYTAB`): domain‑joined browsers and the desktop client authenticate without a password; falls back to the LDAP form. Linux needs `krb5.conf` + keytab; document in `docs/security/active-directory.md`.

**Group → role mapping:** table `idp_group_mappings(workspace_id, provider, group_dn_or_id, role_key, scope_type, scope_id?, team_id?)`: an AD group can grant a `Role` at workspace/project/product/team scope **and/or** create `Membership` in a `Team` (so AD groups can *be* teams, `Team.kind=org`, `settings.synced_from_group`). Membership derived from groups is marked `source=idp` and is removed when the user leaves the group (nightly sync + at login). Manual roles/memberships are never removed by sync. Admin UI in P5 Setup → People & teams → "Identity provider" (mapping table, test login, sync now, last sync log).

**Provisioning:** first login via AD creates `Person` + `WorkspaceMember{status=active}` when the user is in at least one mapped group (or the domain allow‑list), else `invited`. `WorkspaceMember.handle` defaults to `@<sAMAccountName>` (lower‑cased); collisions get a numeric suffix and a review row.

**TLS (HTTPS everywhere, PEM):**
- Single‑node Linux: Uvicorn `--ssl-certfile /etc/tt/tls/cert.pem --ssl-keyfile /etc/tt/tls/key.pem` (full chain in `cert.pem`); or Caddy/nginx termination with the same PEM pair (compose profile `tls`); HTTP → HTTPS redirect; HSTS.
- Internal/enterprise CA: the CLI, desktop, Slack bot and the LLM gateway trust it via `TT_CA_BUNDLE=/etc/tt/ca.pem` (passed to `httpx` `verify=`); `tt login` validates the chain and refuses to proceed on failure (no "skip TLS" flag).
- Service‑to‑service (server ↔ gateway ↔ worker ↔ Postgres) also TLS: Postgres `sslmode=verify-full` with the CA PEM; gateway served over HTTPS.
- Certificate reload without restart (SIGHUP) and expiry alert (P12 observability).

**Tests:** AD simulated with an OpenLDAP container seeded with an AD‑like schema (`sAMAccountName`, `memberOf`) in CI: login success/failure, group → role mapping, nested groups, membership removal on sync, handle collision, LDAPS with a test CA PEM; TLS smoke test (self‑signed test CA, client refuses untrusted, accepts with `TT_CA_BUNDLE`).

### 4.7 Scopes endpoint & URL safety
- `GET /me/scopes` → `[{type, id, key, name}]` for scopes with any readable object; the P5/P6 scope selector uses it.
- Any deep link to an invisible object returns `404` (not `403`) to avoid existence leaks, except for permission failures on objects the actor can read (→ `403`).

## Acceptance criteria
- [ ] Matrix test: for every (role, permission, object‑relationship) combination in the catalogue a table‑driven test asserts allow/deny; ≥ 150 rows generated from the catalogue, no hand‑written expectations drift.
- [ ] `@sara`'s Inbox items are invisible to `@ali` (same team), visible to Sara's line manager, excluded from `GET /items?team=squad-b`.
- [ ] A project manager of `WEB` can see `@sara`'s `no_estimates` habit but **not** a `wellbeing` habit; her line manager sees both; Sara sees both of her own (§13.3).
- [ ] `Event.raw_input` is redacted for a project manager who is not a line manager; present for the line manager and the actor.
- [ ] Adding `@omar` to project `WEB` creates a system read grant; removing him deletes it; a manual grant survives.
- [ ] A `capture`‑scoped token cannot `PATCH /items/{key}` (403) but can post batches.
- [ ] Deep link to an invisible item returns 404; to a visible item without write permission, write returns 403.
- [ ] Every router has at least one visibility test (CI script asserts a `test_acl_*` exists per router module).
- [ ] Performance: list of 10 k items with visibility filter < 150 ms p95 on the dev DB (EXPLAIN shows index use; add indexes as needed).
- [ ] A user in AD group `CN=TT-Platform-Leads` logs in over LDAPS and receives the `manager` role scoped to team `platform` plus team membership; removing them from the group and running the sync removes both; a manually granted role survives.
- [ ] The stack runs over HTTPS with a PEM cert/key on Linux; `tt login` against a server using the internal CA succeeds only with `TT_CA_BUNDLE` set; plain HTTP is redirected; HSTS present.
- [ ] Kerberos SSO (when configured) authenticates a domain user without a password in the e2e harness (`kinit` + SPNEGO), falling back to the LDAP form otherwise.

## Exit checklist
- [ ] All `# TODO(P4-ACL)` markers removed.
- [ ] `docs/security/access-control.md` with the catalogue and visibility order.
- [ ] ADR‑0008 "404 for invisible objects"; ADR‑0022 "Active Directory via LDAPS/OIDC with group → role/team mapping; HTTPS with PEM everywhere".
- [ ] `docs/security/active-directory.md` and `docs/security/tls.md` (PEM layout, CA distribution, rotation).

## Handover notes
P5/P6 must call `acl.visible` through repositories only; routers stay thin. P8 uses `managers_of()` for `Policy.notify.reporting_line`.
