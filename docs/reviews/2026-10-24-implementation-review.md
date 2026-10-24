# Implementation review — branch `claude/listed-plan-implementation-hygguz` @ `584eefb` (2026‑10‑24)

Reviewed against the plans in this repository. Three independent reviewers covered Phases 0–2b, 3–4 and 5–9; their full reports follow the consolidated section. Nothing in the implementation branch was modified. This branch (`claude/optimistic-pasteur-lb1pvw`) carries plans only; the implementation is a separate branch that forked from the first plan commit and therefore predates the later plan additions listed in §3.

## 1. Verdict at a glance

| Phase | Verdict |
|---|---|
| P0 Bootstrap | Done |
| P1 Core domain + capture | Mostly done (RecurrenceRule, attachments storage, progress job, comments/checklists/links/custom fields missing) |
| P2 Reporter CLI, ContextPack, two modes | Done |
| P2b LLM gateway | Mostly done (no real‑model evals, no contract tests, budget keyed by caller‑supplied workspace) |
| P3 Management overlay | Met with gaps (`allowed_roles`, four propagation triggers) |
| P4 Access control, AD, TLS | Partially met — three security defects; Kerberos, Postgres TLS, cert reload deferred by ADR‑0022 |
| P5 Web shell + console | Largely met (no a11y audit, no Storybook) |
| P6 Query API + views | Met with defects (200‑item truncation in layouts/CSV) |
| P7 Snapshot, deck, insights | Partially met — XSS in deck shell, PPTX crash, ACL leaks on shared decks; templates hard‑coded |
| P8 Governance engine | Met with defects (cross‑tenant notifications, no CEL runtime cap, team trend) |
| P9 Governance UI | Partially met (several editors thin; no a11y audit) |

Checks that ran: ruff, mypy (182 files) clean; 2025 DB‑less tests + 1895 targeted DB tests + 1886 overlay/ACL/TLS tests passed on a local PostgreSQL; web lint/tsc/vitest 30/30; presentation build 47 KB with zero external URLs; filter grammar + CEL packages 70 passed. Not run: the full DB suite (timed out in a 10‑minute box on perf tests; one governance test hung idle‑in‑transaction), Playwright e2e, real‑model LLM tests.

## 2. Consolidated findings, ranked (fix in this order)

| # | Sev | Finding | Where |
|---|---|---|---|
| 1 | Critical | Reflected XSS on the public deck shell: path segment spliced unescaped into `window.__DECK_SRC__` inside `<script>`; `/p/t/{token}` needs no login, `/p/{ws}/{pid}` runs with the session cookie | `apps/server/tt_server/api/routers/presentations.py:426-447` |
| 2 | Critical | Any API token can mint an `admin` token for its owner (requested scopes never checked against the presenting token's scopes) | `api/routers/auth.py:58-107` |
| 3 | High | Review‑queue resolve/bulk perform overlay writes on any item in the workspace, including invisible ones, with only "is some line manager" as the gate | `api/routers/overlay.py:1210-1235`, `domain/overlay/review.py:164-375` |
| 4 | High | `DELETE /role-assignments/{id}` and the list are not workspace‑scoped (cross‑tenant revoke) | `api/routers/access.py:117-141`, `db/models/acl.py:36-55` |
| 5 | High | LDAP StartTLS failure ignored → service and user passwords sent in clear when `TT_LDAP_START_TLS=true` with `ldap://` | `integrations/ldap/provider.py:174-178` |
| 6 | High | PPTX export 500s on any slide with empty chart data (zero‑item scope or restricted viewer) | `exports/pptx_builder.py:120-126,243,385-389` |
| 7 | High | Board/pipeline/gantt/calendar/CSV silently truncate at 200 items; WIP counts and breaches wrong at scale | `api/routers/query.py:118-376`, `query/service.py:24` |
| 8 | Medium | Share‑token decks render with the owner's ACL → wellbeing habits, handles and live sessions exposed to anonymous viewers | `routers/presentations.py:399-423` |
| 9 | Medium | Insight/project‑detail slide data bypasses project visibility | `domain/deckdata.py:367-406` |
| 10 | Medium | Notification recipients selected across all workspaces | `governance/notify.py:649-663` |
| 11 | Medium | `GET /metric-snapshots` has no ACL for team/workspace/process scopes; wellbeing key in `habit_scores` | `routers/governance.py:813-823` |
| 12 | Medium | Capture by `item.key` ignores visibility; titles enumerable with a capture token | `domain/capture/ingest.py:203-213,1000` |
| 13 | Medium | Gateway per‑workspace budget keyed by caller‑supplied string | `tt_llm/schemas.py:432`, `routing.py:313-336` |
| 14 | Medium | No CEL evaluation‑time budget; `matches()` allows ReDoS | `tt_cel/runtime.py:545-643` |
| 15 | Medium | Nightly IdP sync strips OIDC‑derived roles when LDAP and OIDC coexist | `integrations/idp.py:47-148,305` |
| 16 | Medium | Default session secret with no production fail‑fast | `settings.py:36`, `api/session.py:25-26` |
| 17 | Medium | Product reads and roll‑ups ignore visibility | `routers/overlay.py:598-671,1270`, `domain/overlay/rollup.py:26-75` |
| 18 | Medium | Deck `?src=` accepts any origin with credentials; `image.url` allows `https:` | `apps/presentation/src/main.js:7-11`, `renderers/misc.js:30-35` |
| 19 | Medium | PDF/PNG exports run Chromium synchronously in the request | `exports/presentation.py:277-326` |
| 20 | Medium | `StageTransitionRule.allowed_roles` not enforced; four propagation triggers missing | `domain/overlay/process.py:183-205`, propagation |
| 21 | Medium | Agent‑mode client uses the 1.5 s translate timeout → always falls back | `tt_client/parser/llm.py:53-71` |
| 22 | Medium | Missing FK/lookup indexes on capture hot paths | migrations |
| 23 | Medium | Team habit trend always `stable`; account lockout is a per‑username DoS in a per‑process dict | `governance/habits.py:525-526`, `webauth.py:39-48` |

Low findings (about 30) are itemised in the slice reports: hard deletes of risk links, stale multi‑cardinality classifications, 500s instead of 400s on bad enum/cursor input, `include_inbox` ignored when nested, hard‑coded gantt baseline, no calendar drag, `blocked_count` hard‑coded, perf budgets missed and relaxed in tests, CI omitting `tt_llm`, and similar.

## 3. Plan features the implementation predates (not defects, but not built)

| plan section | status in implementation |
|---|---|
| P1 §1.2 top‑down plan API and derived statuses | absent |
| P3 §3.11 task ↔ deliverable linkage, plan roll‑ups, plan‑only seed project `ERP` | absent |
| P5 roadmap editor, "Present this plan" | absent |
| P7 plan slide kinds, plan‑only rule (`requires`/`render_empty`), "Project plan review" template, extended snapshot fields | absent |
| P7 §7.5 templates as data (templates are Python constants; `is_template` column never populated) | absent |
| P7 §7.6 external‑LLM generation pack and import validator; P2b `/v1/presentation/author` | absent |
| P4 §4.6 Kerberos SSO, Postgres `verify-full`, certificate hot reload | explicitly deferred (ADR‑0022) — needs product‑owner sign‑off since NT sign‑in is a hard requirement |
| P1 scope: RecurrenceRule job, Attachment storage adapter, `progress_pct` job, Comment/Checklist/Link/CustomField CRUD | tables only |

## 4. Deviations the implementers should record as ADRs

Propagation synchronous in the triggering transaction · permission checks as router dependencies rather than service decorators (plus the fix for finding 3) · workspace `manager` sees `members`‑tier items without membership · Inbox invisible to admins · SHA‑256 token hashing · capture‑side hints as `source=client` · `state_id` tagged overlay but moved by capture · LiteLLM `Router` not used · agent runs in‑memory without storage URL · filter compiler in the server package, CTE instead of ltree for `under`, `EXISTS events` instead of `last_touched_at` · static CEL checker and limits (celpy has no type env or step cap) · snapshot range as `days`+`as_of` · synchronous exports · presentation events on `TargetType.SAVED_VIEW` · nested AD groups by client‑side BFS.

## 5. Recommended sequence for the implementing session

1. Fix findings 1–7 with regression tests, in one security PR; add per‑test timeouts to the DB suite and investigate the governance test that hangs.
2. Fix 8–19 (ACL completeness, exports, CEL budget, sync, secrets) in a second PR.
3. Write the ADRs in §4 so the plans and code agree.
4. Build the §3 items in plan order: P1 plan API → P3 linkage + `ERP` seed → P7 plan kinds and plan‑only rule → P7 templates as data → P7 generation pack + P2b author task → P5 roadmap editor; then the deferred P1 scope.
5. Run the full DB suite, Playwright, and the opt‑in real‑model goldens on a machine with Docker; record coverage per package against the appendix B floors.

---


<!-- ===== slice report: Phases 0–2b ===== -->

# Review: Phases 0, 1, 2, 2b — schema, capture, ContextPack, client/CLI, LLM gateway

## Summary verdict

| Phase | Verdict | One line |
|---|---|---|
| P0 Bootstrap | **Done** | Schema parity proven against the spec file, migrations round‑trip, UoW/Event guard, seed, catalogue, OpenAPI snapshot current. (Plan's "67 models" is a typo: the spec's `ALL_MODELS` has 65.) |
| P1 Core domain + capture | **Mostly done** | Capture pipeline, idempotency, fuzzy match, Inbox routing, ReviewItems, DailyLog, tokens implemented and tested. Missing: RecurrenceRule job/endpoint, Attachment `StorageAdapter`, `progress_pct` recompute job, Comment/Checklist/WorkItemLink/CustomFieldDefinition CRUD. Token‑minting security defect (High). |
| P2 Reporter CLI / ContextPack / translator | **Done** | ContextPack with ETag, glossary/rules/feedback, structured parser, outbox‑file flow with edit/send/--jira, manual form, offline queue, `mode` field (ADR‑0004), golden set. Agent‑mode client timeout bug (Medium). |
| P2b LLM gateway | **Mostly done** | All task endpoints, alias/privacy routing, budgets, bounded agent loop, write tools never offered, injection hardening + grounding, `agent_runs` persistence, 89 scripted tests. Not done: real‑model accuracy/latency, Schemathesis contract run; budget keyed by self‑declared workspace (Medium). |

## Checks run
No Docker daemon; a throwaway PostgreSQL 16 cluster was used via `TT_TEST_DATABASE_URL`.
`uv sync` OK · `ruff check` clean · `ruff format --check` 354 files OK · `mypy` on 182 source files clean · DB‑less suites **2025 passed, 1 skipped** (23 s) · full DB suite timed out at ~20 % in a 10‑min box (perf suites dominate; no failures before the cut) · targeted DB run (unit, integration, capture/idempotency/client/dailylog/tokens/core/ops APIs, CLI↔server and gateway↔server e2e, concurrency) **1895 passed** (75 s) · `tt_client` coverage 89 % (parser 93 %, outbox 94 %) · OpenAPI snapshot up to date. Not measured: server coverage floors. CI omits `tt_llm` from mypy and coverage (`.github/workflows/ci.yml:23-24`).

## Findings

### High
**H1. Token‑scope escalation via `POST /auth/tokens`.** `apps/server/tt_server/api/routers/auth.py:58-107` discards the presenting token (`person, _ = …`, line 69) and never checks `body.scopes ⊆ presenting.scopes`; a leaked `capture` token mints `admin`. `tests/api/test_acl_tokens.py:70-88` tests only the role ceiling. Fix: enforce the subset rule or forbid minting with a bearer token. (Same finding as P4 review C1.)

### Medium
- **M1. Capture by `item.key` ignores visibility and leaks titles.** `domain/capture/ingest.py:203-213` loads any workspace item by key; the result echoes `item.title` (`:1000`) and can attach activities. A capture token can enumerate private‑project titles by guessing keys. Fix: apply `visible()` as `routers/client.py:247,333` does.
- **M2. Gateway budgets keyed by a self‑declared `workspace` string.** `tt_llm/schemas.py:432`, `routing.py:313-317,336`; auth is a deployment‑wide shared secret (`auth.py:464-471`). Fix: derive workspace from the core token or issue per‑workspace gateway tokens.
- **M3. Agent mode uses the 1.5 s translate timeout.** `tt_client/parser/llm.py:53,66-71`, `tt_cli/runtime.py:559-568`; agent p95 budget is 6 s → almost always silently falls back. Fix: separate `agent_timeout` (~20 s).
- **M4. Missing indexes on capture hot paths.** No index on `activities(work_item_id)`, `activities(person_id)` beyond the active partial, `time_entries(person_id, date)`, `evidence(work_item_id)`, `assignments(...)`, `tag_links(target_type, target_id)`, `work_items(assignee_id|reporter_id|state_id)`, `milestones/iterations(project_id)`; used by `ingest._activities` (`:301-309`), `dailylog.build_for_person` (`:56-67`), `resolve_item` (`:241-253`).

### Low
L1 `MissingEventError` on first bearer request after >1 min when a handler commits with no event (`api/deps.py:361-363`, `db/uow.py:49-55`; e.g. `POST /capture/feedback []`) · L2 `X-Agent-Run-Id` trusted from any caller (`routers/capture.py:237`) · L3 capture‑side overlay hints emit `source=client` not `rule` (`ingest.py:74-91, 793`) · L4 `pause`/`done`/`note` with no target creates a spurious Inbox item (`ingest.py:933-979`) · L5 concurrent duplicate `client_id` → `failed/internal_error` instead of `deduplicated` (`ingest.py:895-901` vs unique index) · L6 seed commits stage `wip_limit` and policies unlogged (`tools/seed/demo.py:412,481`, `admin.py:108`) · L7 SHA‑256 token hash vs argon2 (`api/deps.py:311-312`) · L8 ContextPack rebuilt per request, N+1 on glossary (`domain/contextpack.py:75-80, 151-155`) · L9 CI omits `tt_llm` from mypy/coverage · L10 `--jira` auto‑send matches only exact project key (`tt_cli/main.py:57-63`).

No string‑built SQL in the slice; key generation race‑safe (`project_key_sequences` `UPDATE … RETURNING`); placements protected by the gist exclusion constraint and proven by `e2e/test_concurrency.py`.

## Acceptance criteria (condensed)
- **P0**: all Met (parity 65 models, migration round‑trip, schema samples, seed counts 42/4/8/7/4/4, `MissingEventError` guard, OpenAPI diff, catalogue, ADR‑0001). Cannot tell: `make dev` < 5 min, CI green.
- **P1**: Met — §7.1 flow (6 events incl. P3 default stage), idempotency property (40 random msgs), Inbox + ReviewItem, done/reopen, midnight time entry, fuzzy thresholds (0.6 / gap 0.1 / +0.1 recency), DailyLog, catalogue enforcement at append (`events/writer.py:17`), docs + ADR‑0002. Partially — layer guard is an AST test, weaker than SQL introspection; `state_id` moved by capture (spec §12 vs §7 tag). **Missing** — RecurrenceRule, Attachment adapter, `progress_pct` job, Comment/Checklist/Link/CustomField CRUD (ORM only, `db/models/projects.py:410`). Cannot tell — coverage floors.
- **P2**: Met — multi‑item rule, offline exactly‑once, pack contents/size, no‑invented‑entities guard (`parser/llm.py:153-171`), `tt now`/`tt today`, Mode A outbox edit/send/sent (`tt_cli/main.py:112-171`), Jira deferred, Mode B mandatory fields + `mode: manual`, `--from` round trip, air‑gapped import, docs + ADR‑0003. Partially — real‑model golden accuracy not measured; no `from_feedback/` script.
- **P2b**: Met — `local_only` network‑deny test, golden via gateway (scripted model), agentic capture ≤ 4 tools + send, batch review merge/dup, injection suite, 429 + fallback (see M2), suggestions/insights via gateway, alias swap config‑only, limits + `agent_runs`/`agent_steps`/`budgets`, write tools never offered (stricter than plan), all task endpoints (`app.py:347-391`), docs/prompts/ADR‑0018/19/20. **Missing** — Schemathesis contract run, real‑model p95.

## ADR candidates
1. Capture‑side hints as `source=client` vs `rule`. 2. SHA‑256 token hashing. 3. `state_id` overlay tag vs capture moves (exemption relied on by `test_layer_guard.py`). 4. LiteLLM `Router` not used; fallbacks/retries/budgets hand‑rolled on `litellm.acompletion`. 5. Agent runs in‑memory when `storage.url` unset. 6. `review_items` in migration 0002 not 0003.

## Test coverage gaps
Scope‑ceiling on token minting (H1); capture‑by‑key visibility (M1); budget with varying `workspace` (M2); agent timeout (M3); `MissingEventError`/`last_used_at` interaction (L1); concurrent duplicate `client_id` (L5); Hypothesis not used where the plan asked (idempotency, adversarial translator); per‑package coverage floors not asserted; real‑model goldens and Schemathesis never run; unimplemented P1 features untested.


<!-- ===== slice report: Phases 3–4 ===== -->

# Review: Phase 3 (Management overlay) and Phase 4 (Access control, AD/LDAPS, HTTPS/PEM)

## Summary verdict

| Phase | Verdict | One line |
|---|---|---|
| P3 Management overlay | **Met with gaps** | Core mechanics (mirrors, single-cardinality replace, exclusion constraint + row lock, stage↔state sync with guard, approvals incl. rollback, weight grammar, propagation idempotency, review-queue ownership, bulk preview==apply, If-Match 409, provenance) are implemented and pass their seeded tests. Missing: `allowed_roles` enforcement, `project.visibility_changed / process_changed / item.team_changed / product.risk_linked` triggers, job form of propagation (synchronous instead, documented). |
| P4 Access control | **Partially met, with 3 security defects** | `visible()`/`needs()` used broadly; Inbox/wellbeing/redaction/token-scope/404-vs-403 tested and passing. But: (1) a `capture`-scoped token can mint an `admin` token, (2) review-queue resolve has no ownership/permission check, (3) role-assignment revoke is not workspace-scoped. StartTLS result unchecked (plaintext downgrade). Kerberos, Postgres `verify-full`, cert hot-reload explicitly not built (ADR-0022 / docs). |

## Checks run
- Docker unusable in this environment; used local PostgreSQL 16 via `TT_TEST_DATABASE_URL` with `pg_trgm`, `ltree`, `btree_gist`.
- First run including governance tests **hung** after 117 passes (`idle in transaction` on `INSERT INTO violations` inside `tests/api/test_acl_governance*.py`); killed after ~9 min.
- Second run excluding governance/llm/presentations/query/console/snapshot: **1886 passed, 156 deselected, 112 s, exit 0** (ACL matrix 1772 cases, identity 22, weights/approvals 21, overlay seeded 21, TLS 6 with real uvicorn + throw-away CA, review property 6, tokens 6, …).
- `tests/e2e/test_concurrency.py` not run (needs e2e harness); constraint + lock reviewed statically.
- No `# TODO(P4-ACL)` markers remain.

## Findings

### Critical
**C1. Any API token can mint an `admin`-scoped token for its owner (scope escalation).** `apps/server/tt_server/api/routers/auth.py:69` (`create_token`) discards the authenticating `ApiToken`; requested scopes validated only against `TOKEN_SCOPES` (`auth.py:77-84`). A leaked `capture`-only CLI token (default from `/auth/ldap/token`, `webauth.py:177-179`) can `POST /auth/tokens {"scopes":["admin"]}`. `tests/api/test_acl_tokens.py:70-87` checks only the owner ceiling. Fix: require `max(requested) <= max(token.scopes)` or forbid bearer-token minting (session/`admin` only); add negative test.

### High
**H1. Review-queue `resolve`/`bulk` bypass ownership and object permissions.** `overlay.py:1210-1235` gate only on `needs_review()` (`api/authz.py:47-59`: `review_queue.resolve` or "is anybody's line manager"). `domain/overlay/review.py:164-179` (`_load`) fetches any `ReviewItem`/`Classification` in the workspace by id without `visible()`; `resolve` (`review.py:205-237`) never calls `review_owner()` nor checks `item.write_overlay`/`classification.confirm`; `_apply_choice` (`review.py:240-375`) then moves/assigns/stages/classifies/links risks/sets estimates/merges tags. Any line manager of one person can mutate any item in the workspace, including invisible ones. Fix: in `review.resolve` load through visible lookup and assert `review_owner(...) == actor_id` or `acl.can("review_queue.resolve", item_ref)`; negative test.

**H2. `DELETE /role-assignments/{aid}` not workspace-scoped (cross-tenant).** `access.py:134-141` uses `session.get(RoleAssignment, aid)`; `RoleAssignment` has no `workspace_id` (`db/models/acl.py:36-55`). A `role.admin` in workspace A can revoke assignments in workspace B. `list_assignments` (`access.py:117-131`) also returns other-workspace scoped assignments. Fix: resolve the assignment's scope to the workspace (cf. `integrations/idp.py:39-44`) or add `workspace_id`.

**H3. LDAP StartTLS failure ignored → credentials in plaintext.** `integrations/ldap/provider.py:176-178`: `conn.open(); conn.start_tls()` result discarded; with `raise_exceptions=False` (`:174`) ldap3 returns `False` and leaves the socket clear; subsequent service and user binds go unencrypted. Only `TT_LDAP_START_TLS=true` with `ldap://`; `ldaps://` is fine (`CERT_REQUIRED`, `:159-163`). Fix: raise when `start_tls()` returns False.

### Medium
- **M1.** `POST /idp/sync` / nightly sync hardcodes provider `"ldap"` (`integrations/idp.py:305`) but removes every `source="idp"` row not in `want` (`idp.py:102-108, 133-136`) → OIDC-derived roles/memberships stripped nightly when both providers coexist. Fix: stamp `source="idp:<provider>"`.
- **M2.** `StageTransitionRule.allowed_roles` never enforced (`db/models/process.py:103`; `place()` checks only `required_evidence_kinds`, `domain/overlay/process.py:183-205`).
- **M3.** Propagation trigger table incomplete: no `project.visibility_changed`, `project.process_changed`, `item.team_changed`, `product.risk_linked` handling (`PATCH /projects` emits only `project.updated`, `core.py:~420-426`). `docs/api/propagation.md` is honest about it.
- **M4.** Default session secret `"dev-insecure-session-secret"` (`settings.py:36`, `.env.example:22`) with no fail-fast when `env != dev` (`api/session.py:25-26`).
- **M5.** Permission checks live in routers (`api/authz.py:62-79`, `overlay.py`), not services; H1 is the consequence. ADR + sweep of service entry points reachable from >1 route.
- **M6.** Product visibility not applied on reads: `GET /products` (`overlay.py:598-608`), `/products/{ref}/links` (`:671`), `_product()` (`:610`) ignore `visible(.., Product)` though `product_visible` exists (`acl/visibility.py:109-117`); `GET /classifications?target_type=project|product` (`overlay.py:344-382`) applies visibility only to work items.
- **M7.** Roll-ups aggregate over invisible descendants (`domain/overlay/rollup.py:26-75`; `GET /items/{k}/rollup` `overlay.py:1270` checks only the root).
- **M8.** Account lockout: 5 failures per username lock for 5 min, in-process dict (`webauth.py:39-48`) → DoS vector, not shared across workers/restarts.

### Low
L1 `risks.unlink_item` hard-deletes `RiskLink` (`risks.py:216-230`) and removes project-level inherited links too · L2 `inherit_for_new_item` (`risks.py:245-278`) skips product-level risks · L3 `set_container_priority` emits `project.priority_changed` even when unchanged (`propagation.py:176-186`) · L4 bulk stage edit and review-queue placement do not recompute weights (`bulk.py:148-160`, `review.py:291-304`) · L5 mirror value keys for iteration/milestone/release/component are `row.id[:8]` (`mirrors.py:198`) · L6 `scope_members` tier unimplemented (`acl/visibility.py:51-64`) · L7 `stage.moved` uses `entity_type=target_type` (`process.py:258-260`) · L8 OIDC accepts missing `email_verified` (`oidc.py:108`) · L9 refreshed cookies lose `Secure` behind a TLS proxy (`main.py:119`) · L10 `ItemPatch.severity` unvalidated → 500 at flush (`overlay.py:1282`); `team_id`/`assignee_id` not checked against workspace · L11 tokens hashed SHA-256 not argon2 (`api/deps.py:27-28`) · L12 stale inherited `objective` classifications survive `change_project` (`propagation.py:290-313`, `classify.py:97-98`).

## Acceptance criteria — Phase 3
All listed criteria **Met** with tests passing (mirrors; WEB-101 stage↔state sync both directions, `sync_depth` guard `process.py:274-408`; exclusion constraint `db/models/process.py:86-95` + row lock `process.py:127-135`; DATA-R3 inheritance; GOV propagation (container event re-emitted, L3); default scheme reproduces 42 seed weights; approvals rollback; review-queue scoping and never-twice property test; glossary learning; capture completion in one correlation `propagation.py:248-348`; bulk preview==apply; layer/overlay guards). Coverage floor not measured. In scope but **Missing**: `allowed_roles` (M2), trigger rows (M3), `work_item_rollups` job form (documented P12).

## Acceptance criteria — Phase 4
Met: ACL matrix (1772 cases); Inbox privacy (admins excluded too); wellbeing habit rule; `raw_input` redaction; system grants follow membership; capture token cannot PATCH (but see C1); 404/403 rule; per-router ACL tests (declaration list); AD group → role+team mapping with removal on sync (ldap3 in-memory, not OpenLDAP); nested groups (client-side BFS); handle collision; LDAPS CA bundle; HTTPS from PEM with uvicorn + 308 redirect listener, HSTS, compose `server-tls` profile; `TT_CA_BUNDLE` honoured by CLI, gateway, server→gateway. Cannot tell: 10k-item p95 threshold. **Missing**: Postgres `sslmode=verify-full` and service-to-service TLS (`docs/security/tls.md` says "Not yet"); Kerberos/SPNEGO (explicit decision in ADR-0022; plan said "when configured").

## Independent review notes
Workspace scoping good except H2 and `AclContext.load_context` loading other-workspace assignments (`acl/context.py:133-135`). No SQL injection found; LDAP filters escaped (`provider.py:84`). Login: unknown user vs wrong password both 401, slight timing difference; workspace slug enumeration via 404 vs 401 (`deps.py:116-121`). CSRF double-submit for cookie auth; OIDC `state`/PKCE/`iss,aud,exp,nonce` verified (`oidc.py:95-113`); open-redirect guard; CLI login loopback-only.

## ADR candidates
1. Propagation synchronous in the triggering transaction (not a job). 2. Router-level `needs()`/`check_*` + `visible()` via `uow.context["acl"]` instead of service decorators (plus the H1 fix and the rule "service entries reachable from >1 route check themselves"). 3. Workspace `manager` sees `members`-tier items without membership (`visibility.py:43-47, 69-71`). 4. Client-side nested-group BFS (in ADR-0022). 5. Kerberos, cert hot-reload, Postgres `verify-full` deferred (needs product-owner sign-off since NT sign-in is a hard requirement). 6. SHA-256 token hashing. 7. Approval-rejection rollback as `source=overlay`. 8. Inbox invisible to admins (confirm audit path).

## Test coverage gaps
Lower-scoped token minting higher scope (C1); non-owner / invisible-item review resolve (H1); cross-workspace role-assignment delete (H2); StartTLS failure (H3); LDAP+OIDC coexisting sync (M1); `allowed_roles` (M2); visibility/process/team propagation (M3); product/rollup/classification visibility (M6, M7); `scope_members` (L6); overlay coverage floor; governance test hang (`test_acl_governance*.py`) needs a per-test timeout.


<!-- ===== slice report: Phases 5–9 ===== -->

# Review: Phases 5–9 (web shell, query/views, snapshot/presentation/insights, governance engine, governance UI)

Checkout HEAD `584eefb`. Note: the implementation branch forked from the first plan commit, so the later plan additions (plan slide kinds, plan‑only rule, "Project plan review" template, templates‑as‑data, generation pack, extended snapshot fields) are **not present** in it — every item in that list is Missing by construction, not by omission.

## Summary verdict

| Phase | Verdict | One line |
|---|---|---|
| P5 Web shell & console | **Largely met**, two exit items missing | Shell, setup editors, SSE review queue, drawer with provenance/If‑Match/undo present; no Storybook/Ladle, no Lighthouse/axe audit. |
| P6 Query API & views | **Met with defects** | Grammar covers every §9 key, parameterised, ACL on every statement, Hypothesis + injection tests. Layout endpoints and CSV silently truncate at 200 items; calendar drag and gantt baseline picker missing in web. |
| P7 Snapshot / presentation / insights | **Partially met** | Snapshot field‑for‑field with prototype; deck is a 47 KB self‑contained build with zero external URLs; exports and per‑slide ACL exist. But: reflected XSS on the public deck shell (Critical), PPTX crash on empty chart data (High), insight slides bypass project ACL, share token leaks wellbeing data, templates are code not rows, PDF/PNG rendered synchronously in‑request. |
| P8 Governance engine | **Met with defects** | Context schema v1, 11 YAML templates, dry‑run + activation gate, on_event + scheduled with `tt_filter`, grace via `policy_match_state`, partial unique index, auto‑resolve, auto_action, notifications, metrics, 12 habit detectors, oracle test (36). Defects: cross‑workspace role notifications, no runtime CEL cost cap, team habit trend never computed. |
| P9 Governance UI | **Partially met** | Template → form, CodeMirror CEL editor with 400 ms check, inbox a/r/w/f + waive modal, habit report with restricted wellbeing and manager‑only coaching note, weights preview, bell. Missing: scope picker, candidate‑filter builder, full notify/auto‑action editors, version history, sparklines, metric‑snapshot ACL, Storybook. |

## Checks run
`pnpm install` OK (337 pkgs) · web lint OK · vitest 30/30 · `tsc --noEmit` OK · presentation tests 10/10, build `dist/presentation.html` 47.3 KB with **0** `http(s)://` strings · `uv run pytest packages/filter_grammar packages/cel_policies` 70 passed · ad‑hoc PPTX build from empty snapshot **crashes** · ad‑hoc XSS probe on `/p/t/{token}` **reflected inside `<script>`**. Not run: server pytest suite, Playwright e2e, perf tests.

## Findings

### Critical
**C1. Reflected XSS on the deck shell (unauthenticated).** `apps/server/tt_server/api/routers/presentations.py:440-447` builds `window.__DECK_SRC__="…/{token}/data"` by f‑string; `_shell()` (426‑437) splices it into `<head>`. Path segments are percent‑decoded, so `/p/t/x%22;alert(document.domain);%2F%2F` returns executable script (verified). `/p/t/*` needs no login; `/p/{ws}/{pid}` has the same bug with the session cookie present → account takeover on the console origin. Fix: `json.dumps(url).replace("<", "\\u003c")` (as `exports/presentation.py:261-266` already does) or validate segments against `^[A-Za-z0-9_-]+$`; add a test.

### High
**H1. PPTX export 500s when a 1‑dim `explorer` or `chart` slide has no data.** `exports/pptx_builder.py:120-126` (`_bar_chart`) called with empty categories from `:243` and `:385-389` → python‑pptx `ValueError` → unhandled 500. Any zero‑item scope or restricted viewer hits it. Fix: guard empty categories with a "No data in scope." text box; add empty‑snapshot test.

**H2. Layout endpoints and CSV silently truncate at 200 items; WIP counts wrong at scale.** `routers/query.py:194-201` (board), `225-227` (pipeline), `270-272` (gantt), `376` (calendar), `118-125` (CSV) use `page={"size": MAX_PAGE}` (`query/service.py:24` = 200); pipeline `wip_count`/`over_wip` (`:251`) and board (`:214`) computed from the truncated list. Fix: use `service.bulk_items()` (`service.py:286`) or `COUNT … GROUP BY` for counts and page only cards.

### Medium
- **M1.** `insight`/`project_detail` slide data bypasses project ACL (`domain/deckdata.py:367-406`, lookup by key without `visible()`); only the client filters (`renderers/misc.js:43-47`).
- **M2.** External share token renders with the owner's ACL (`routers/presentations.py:399-423`) → anonymous viewers get wellbeing `habits`, violation `person` handles, `now` sessions (`assembler.py:388-422`). Drop wellbeing rows for token viewers; consider redacting `people`/`now`.
- **M3.** Cross‑workspace notification recipients: `governance/notify.py:649-663` selects `RoleAssignment` by role key across all workspaces (no `workspace_id`, `db/models/acl.py:36-52`), accepts `scope_type in (WORKSPACE, NONE)` without `scope_id == ws.id`.
- **M4.** `GET /metric-snapshots` has no ACL for team/workspace/process scopes (`routers/governance.py:813-823`); `MetricSnapshot: managers_only` policy exists (`acl/visibility.py:265`) but is never applied; `habit_scores` include wellbeing key `long_sessions` (`metrics.py:122-130,215`).
- **M5.** No runtime CEL cost cap; `matches()` compiles user regexes → ReDoS (`tt_cel/runtime.py:545-547,630-643`; `celpy/evaluation.py:409`). Static limits only. Add a wall‑clock budget per evaluation; restrict or reject nested‑quantifier patterns.
- **M6.** Team‑level habit trend never computed: `governance/habits.py:525-526` keys `prev` by person id while `sid` may be a team id → always `stable`.
- **M7.** PDF/PNG exports run headless Chromium synchronously in‑request (`exports/presentation.py:277-326`; `routers/presentations.py:345-362`); plan says job → download URL. Bound concurrency or enqueue.
- **M8.** Deck `?src=` overrides server data URL with any origin, fetched with credentials (`apps/presentation/src/main.js:7-11`); `image.url` permits `https:` (`renderers/misc.js:30-35`) → exfil beacon, contrary to "no external requests". Honour `?src` only when `__DECK_SRC__` unset and same‑origin; restrict `image.url` to `data:`/same‑origin.

### Low
L1 `deckdata.py:336` mutates `qc.scheme_id` for a `view` slide, leaking into later slides · L2 `int(page["cursor"])`/`int(page["size"])` and enum casts on client input → 500 instead of 400 (`query/service.py:166-167`, `presentations.py:510`, `governance.py:578-582`) · L3 `include_inbox` only honoured at top level (`compile.py:148-151`); `{"not": {"include_inbox": true}}` compiles to `NOT true` · L4 `SavedView` sharing ignores `scope`/`view.read` (`acl/visibility.py:243-244`) · L5 `blocked_count: 0` hard‑coded (`metrics.py:211`) · L6 no habit sparkline · L7 web gantt hard‑codes `baseline: "baseline-1"`, no zoom; API gantt `rows: "milestone"` unhandled (`routers/query.py:313`) · L8 calendar has no drag‑to‑day · L9 non‑item violations fall back to raw UUID label (`assembler.py:376-377`) · L10 dead code `habits.py:319-321` · L11 snapshot perf budget missed: documented 2.7–6 s cold / 0.5 s cached; test asserts 8 s / 1 s (`tests/api/test_snapshot_perf.py:51-52`).

## Acceptance criteria (condensed)
- **P5**: Met — scoped review queue (`review.py:157-161`), provenance chips (`ItemDrawer.tsx:34,123,159`), generated‑client lint rule (`eslint.config.js:15-17`), docs + ADR‑0009. Partially — 409 flow (revert‑via‑events not asserted). Cannot tell — ≤ 6 clicks, Playwright in CI. Missing — Lighthouse ≥ 95, Storybook.
- **P6**: Met — all §9 keys (`tt_filter/parse.py:16-19`), ACL via `visible()` on every statement (`query/service.py:118`), weight scheme independence, filter‑grammar docs + schema, reuse by P8 (`governance/engine.py:223,339`), ADR‑0010. Defect at scale — WIP breach (H2). Partially — gantt baseline in web, CSV (truncation). Cannot tell — p95 budget.
- **P7**: Met — deck navigation/deep link (`deck.js:175-231,241-245`), snapshot ACL (`assembler.py:364-404`, `deckdata.py:293-313`), no external URLs in build, table toggle/keyboard/reduced motion, grounded insights (`insights.py:91-121`), schemas committed, docs + ADR‑0011/0021, 19 slide kinds, snapshot shape. Partially — "Add to presentation" only on views, exports (H1, M7), perf (L11). Deviation — templates hard‑coded (`domain/presentations.py:137-179`), `is_template` column never populated.
- **P8**: Met — oracle (36), 7 CEL examples, typo rejection (`checker.py:366-377`), candidate filter in SQL (`engine.py:321-345`), template copy semantics, wellbeing visibility, KeyResult from nightly, `auto_when` (`runner.py:784-861`), 11 templates, dry‑run gate (`governance.py:395-498`), de‑dup index (`0007_governance_runtime.py:71-77`), grace/auto‑resolve/auto_action/notify (M3), 12 habit definitions, docs drift test. Partially — metrics (`blocked_count`), team trend (M6).
- **P9**: Met — template form without CEL, inline type errors, inbox actions, habit rules + coaching note manager‑only (`governance.py:679-684`, `Habits.tsx:406-424`), weight preview = server, digest, bell, docs + ADR‑0014. Partially — table toggles (Performance.tsx), scope picker / candidate builder / notify & auto‑action editors / version history. Missing — a11y audit, Storybook.

## ADR candidates
1. Filter compiler in `apps/server/tt_server/query/compile.py`, package holds parse/validate/schema/dates. 2. `dims.under` via recursive CTE, `touched` via `EXISTS events` (`compile.py:214-223,335-346`). 3. Static CEL checker over lark tree + static cost limits (celpy has neither) — residual risk M5. 4. Templates as Python constants (superseded by the new §7.5 plan: templates as data). 5. Snapshot range as `days`+`as_of`; cache per viewer. 6. Synchronous exports (only with concurrency bounds). 7. Presentation events on `TargetType.SAVED_VIEW`. 8. Notification de‑dup per (violation, person, day, channel).

## Test coverage gaps
XSS in deck shell (C1); PPTX on empty scope (H1); counts above 200 items / CSV completeness (H2); insight slide ACL and public‑token wellbeing (M1, M2); multi‑workspace notify (M3); metric‑snapshots as non‑manager (M4); CEL evaluation‑time budget with pathological `matches()` (M5); team habits across two windows (M6); no axe/a11y assertions despite two "≥ 95" criteria; no vitest for the builder beyond `slides.test.ts`.

Repro scripts: `scratchpad/pptx_empty.py`, `scratchpad/xss_probe.py`.
