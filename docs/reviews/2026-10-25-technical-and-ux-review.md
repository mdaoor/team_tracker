# Technical and UX review — implementation branch `claude/listed-plan-implementation-hygguz` @ `472650d` (2026‑10‑25)

Two independent reviews of the current implementation head, which now includes the fixes and feature packages from PRs 4–9 (security fixes, plan API and linkage, plan slides, templates as data, generation pack, remaining scope). Part 1 is a code‑level technical review. Part 2 is a UX review performed by booting the full stack with the demo seed and using it as the three personas, with Playwright screenshots at desktop and mobile widths and an axe‑core scan on every screen. Neither review modified the implementation branch. The plan‑conformance review from 2026‑10‑24 is in `docs/reviews/2026-10-24-implementation-review.md`; its two critical findings are verified fixed (Part 1 §Status).

## Combined executive summary

| Dimension | Grade | Headline |
|---|---|---|
| Technical overall | **B** | Sound spine (UnitOfWork + event guard + transactional outbox, DB‑level invariants, fail‑fast settings, strict typing, 0 TODOs, 40 ADRs). Weaknesses are structural debt rather than defects: fat routers holding domain workflows, domain modules importing the HTTP error type, 60 unindexed FK columns on hot paths, no retry on event‑driven jobs, a proxy‑blind rate limiter, non‑hermetic tests, and a frontend that discards its own generated types. |
| UX overall | **C+** | Every surface in the UX document exists and the deep plumbing works (filter grammar and URL round trip, drill‑down with Backspace, 409 conflict dialog, WIP breach, deck keyboard map, print/PDF/PPTX server‑side, plan‑first ERP deck, restricted wellbeing). What breaks trust is the last mile: every download button in the web UI throws, the builder preview is a blank iframe, delete has no confirmation, raw UUIDs appear in headings and chips, the review queue has no one‑click default and routes a reporter's lines back to herself, and nothing adapts below ~1000 px. |

**Do first (both reviews agree these are small and high‑impact):** fix the web download path (`postBlob` double‑read), allow the deck to be framed by the builder, add the FK index migration, make tests hermetic to `.env`, add retries to event‑driven jobs, make the rate limiter proxy‑aware, resolve names instead of UUIDs in habits/drawer/provenance/digest, confirm‑and‑undo on destructive actions, gate navigation on permissions, and dedupe toasts. Then the medium items: responsive shell, review‑queue suggestions and line‑manager routing, capture intent for verb‑less lines, Gantt renderer, router/service extraction, typed API wrappers.

---

# Part 1 — Technical review

Read‑only review. Runs performed: web build + vitest, DB‑less package coverage, gateway/slack coverage, server suite against local Postgres 16 time‑boxed to 12 min (306 of 442 tests completed), OpenAPI + `schema.d.ts` regeneration diff, AST scans (import cycles, loops‑with‑queries, docstrings, duplicate blocks), vulture, `pg_catalog` queries on the migrated schema.

## Executive summary

| Area | Grade | One line |
|---|---|---|
| Architecture | B- | Layers hold (db→domain→api, no db→domain imports) and the UoW/Event guard is the real spine, but routers orchestrate domain workflows, 27 domain/acl/integration modules raise the HTTP `ApiError`, and the UoW is bypassed in ~10 read paths that write builtins. |
| Data model | B | Uniqueness/exclusion are in the DB where it matters (keys, placements, client_id dedup); 10 clean, reversible migrations; but 60 FK columns are unindexed (including per‑request ACL paths), enums are VARCHAR with no CHECK, and no FK has an ON DELETE rule. |
| Correctness | B | One transaction per capture message with IntegrityError retry and transactional outbox; weak spots are server‑local `date.today()` in 4 business paths, no retry on event‑driven jobs, float arithmetic on weights/money in habits/metrics. |
| Performance | B- | Snapshot assembler and layouts batch properly (`IN` chunks, internal paging), caching and render bounding exist; but ACL context costs 5+ queries per request on unindexed tables, periodic jobs build a new engine per run, and the web ships one 720 kB chunk. |
| Security | B+ | Both criticals fixed with tests; CSP/HSTS/CSRF/HttpOnly/HMAC webhooks/Fernet credentials present; remaining: rate limiter is proxy‑blind (org‑wide sign‑in lockout), `'unsafe-inline'` CSP on shells, root single‑stage Docker image with no `.dockerignore`, Fernet key falls back to the session secret. |
| Operability | B- | structlog everywhere (0 prints), `/readyz` checks DB + job schema, settings fail fast on prod misconfig; but OTel is a bare TracerProvider with one span and no FastAPI/SQLAlchemy instrumentation, worker runs at concurrency 1, no job dead‑letter alerting. |
| Tests | B | 442 server + 81 package + 59 gateway + 13 slack + 49 web unit + 27 web e2e tests, behaviour‑first, Hypothesis, Schemathesis; but the suite is slow (~2.3 s/test), 3 tests fail on a dev machine with a `.env`, and concurrency tests share the per‑test savepoint connection. |
| Frontend | C+ | Generated OpenAPI types are fresh but unused beyond constructing the client: 233 uses of `Row = Record<string, any>`; no ErrorBoundary, no code‑splitting; axe e2e on 14 screens is good. |
| LLM gateway | B+ | Versioned prompt files, JSON‑schema‑constrained output with one repair retry, per‑call timeouts, daily USD budgets in Postgres, tool results truncated, bounded agent loop; budget check is check‑then‑charge (TOCTOU) and costs are floats; real‑model goldens still never run. |
| Code quality | B | 0 TODO/FIXME, ruff + mypy `--strict` clean, 180 `type: ignore` (mostly tests), 44 % docstrings on public service functions, 18 duplicated 8‑line blocks, dead code negligible. |

## Top 15 technical risks (ranked)

1. **Rate limiter is proxy‑blind → org‑wide sign‑in lockout.** `apps/server/tt_server/main.py:132-147` keys `/auth/*` POSTs on `request.client.host`; `ratelimit.py:40-47` never reads `X-Forwarded-For`. Behind the documented Caddy/nginx/Helm ingress every user shares one 60/min bucket. Fix: honour XFF only from `settings.trusted_proxies`. **S**
2. **`Settings` reads `.env` inside tests → non‑hermetic prod‑config tests.** `settings.py:26`; `tests/api/test_acl_hardening.py:156`, `tests/api/test_review_b.py:196-199` fail with a developer `.env` (reproduced). Fix: `Settings(_env_file=None)` in tests or an autouse override. **S**
3. **60 FK columns without indexes, several on per‑request paths.** `project_members.person_id/project_id`, `memberships.person_id/team_id`, `assignments.*`, `events.actor_id`, `daily_logs.person_id`, `estimates.work_item_id`, … `acl/context.py:145-163` hits `project_members` and `memberships` on every authenticated request. Fix: migration `0011_fk_indexes` + a test asserting every FK has a leading index. **S/M**
4. **UoW bypassed on read paths that write.** `api/routers/governance.py:66,752`, `presentations.py:142`, `presentation_templates.py:37` call `ensure_builtins` then `session.commit()` inside GET handlers; `domain/deckdata.py:96,293`, `domain/insights.py:230`, `governance/runner.py:49`, `api/routers/privacy.py:83` commit a raw session, skipping the `MissingEventError` guard. Fix: seed builtins at migration/startup; grep‑test for `session.commit(` outside `db/uow.py`. **M**
5. **Event‑driven jobs have no retry → silent loss.** `jobs/governance_tasks.py:26`, `jobs/llm_tasks.py:37` declare no `RetryStrategy` (only `jira.on_event` does). Fix: exponential retry + `tt_jobs_failed` metric with alert. **S**
6. **Domain orchestration lives in routers.** `api/routers/overlay.py` 1,397 lines / 60 handlers; `patch_item` (`:1310-1397`) sequences mirrors, propagation, plan linkage, weight recompute and three events. `core.py` (982) and `governance.py` (1,010) likewise. Fix: extract `items.update_overlay(uow, actor, item, patch)` etc.; split routers by resource. **M**
7. **Domain depends on the API layer; two import cycles.** 27 modules under `domain/`, `acl/`, `governance/`, `integrations/`, `exports/` import `tt_server.api.errors.ApiError`; cycles `domain/overlay/process.py:28 ↔ domain/projects/service.py:107` and `api/authz.py ↔ api/routers/core.py`; 123 function‑level imports. Fix: `domain/errors.py` mapped to HTTP in one handler. **M**
8. **Server‑local `date.today()` for business dates.** `api/routers/core.py:599`, `presentation_templates.py:266`, `acl/reporting.py:37,62` (an access decision), `governance/runner.py:237`. Fix: `ws_today(ws)` helper + ruff ban. **S**
9. **Container image: root, single‑stage, no `.dockerignore`, unpinned `uv:latest`.** `apps/server/Dockerfile:1-10`, `apps/llm_gateway/Dockerfile`. Helm mitigates in k8s only. **S**
10. **Test harness vs concurrency tests; suite speed.** `tests/api/test_review_b.py:377-399` gathers over the per‑test savepoint connection → `InvalidSavepointSpecification` (reproduced; yesterday's hang is the same family). 306 tests in 713 s. Fix: committed‑engine fixture with TRUNCATE for concurrency tests; template DB for schema. **M**
11. **Frontend type erasure.** `apps/web/src/api/http.ts:9` `Row = Record<string, any>` used 233 times; generated `paths`/`components` consumed nowhere. Fix: typed per‑endpoint wrappers for the 10 mutating pages first. **M**
12. **No ErrorBoundary, no code‑splitting.** `App.tsx:33-66` eager routes; one 719.6 kB chunk (231.7 kB gzip) including CodeMirror for everyone; a render error blanks the app. Fix: `React.lazy` per route group + boundary per outlet. **S**
13. **CSP on HTML shells permits `'unsafe-inline'`.** `main.py:52-55`; inline scripts at `presentations.py:707`, `exports/presentation.py:36`. Fix: per‑response nonce. **S/M**
14. **Gateway budget TOCTOU and float money.** `tt_llm/routing.py:207-216` checks, `:254` charges after completion; `cost_usd: float` vs `Numeric(18,6)`. Fix: reserve‑then‑settle with `Decimal`. **S/M**
15. **Enum and cascade invariants only in app code.** `native_enum=False` without CHECK (`db/models/acl.py:41-47`; 1 CHECK in the whole schema); none of 176 FKs declares `ondelete`; hard deletes at `governance/apply.py:140`, `governance/metrics.py:158`, `domain/overlay/risks.py:223-229`, `webauth.py:71`. **M**

Below the top 15: Fernet key derives from `secret_key or session_secret` (`integrations/jira/crypto.py:14`) — make `TT_SECRET_KEY` mandatory outside dev; periodic jobs construct a new engine per run (`jobs/tasks.py:21-22,58,80,99,113`); OTel has one span and no instrumentors; worker concurrency 1 (`apps/worker/tt_worker/__main__.py:11`); `tt_slack/bolt_app.py` 0 % covered; floats on `Numeric` paths in `governance/habits.py:267-272`, `governance/metrics.py:46-205`; 29 untyped reads of `ws.settings` JSONB.

## Status of the 2026‑10‑24 critical findings

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Reflected XSS in deck shell | **Fixed** | `api/routers/presentations.py:713-722`: segments must match `^[A-Za-z0-9_-]{1,128}$` else 404; literal is `json.dumps` with `<>&` escaped; `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, CSP `frame-ancestors 'none'`. Tests `tests/api/test_review_a.py:27,37`. |
| 2 | Any token can mint an `admin` token | **Fixed** | `api/routers/auth.py:84-92`: scope ceiling enforced against the presenting token (403 `scope_ceiling`); test `test_review_a.py:52`. Session‑cookie callers have no ceiling (correct). |

Also verified fixed in passing: role assignments workspace‑scoped (`0009_review_fixes.py`, test `test_review_a.py:90`); DB‑backed lockout (`webauth.py:48-66`); bounded renders (`presentations.py:593-622`); CEL limits (`tt_cel/runtime.py:23-25,124-151`); capture‑path indexes present.

## Metrics

| Metric | Value |
|---|---|
| LOC Python | server 30,371; server tests 11,570; gateway 4,564; slack 917; cli 634; schema 1,999; client_core 2,493; filter 741; cel 1,358; tools 1,419 |
| LOC TS/JS | web `src` 24,337 (15,900 generated `schema.d.ts`, ~8,400 hand‑written); presentation 1,843; desktop 994 |
| Tests | server 442 (api 292, seeded 57, unit 48, integration 26, e2e 16, contract 2); packages 81; gateway 59; slack 13; web vitest 49; web Playwright 27; presentation+desktop vitest 28; presentation Playwright 12 |
| Results here | packages 402 passed; gateway+slack 107 passed, 1 skipped; web vitest 49/49, 0 console errors; server 303 passed, 3 failed (2× `.env` leak, 1× savepoint race), interrupted at 306/442 after 12 min |
| Coverage | schema 98.6 %, filter 94.8 %, cel 90.0 %, client_core 78.5 % (`parser/llm.py` 43 %, `feedback.py` 36 %), gateway+slack 87 % (`bolt_app.py` 0 %); server not re‑measured (implementers self‑report 90 %) |
| Migrations | 10, all with real `downgrade()`; `0001` never edited after creation; one data migration (`0009`) |
| Schema | 87 tables, 189 indexes, 176 FKs (0 with ON DELETE), 25 UNIQUE, 1 EXCLUDE, 10 partial unique, 1 CHECK (procrastinate), 61 JSONB columns, 60 unindexed FK columns |
| Largest Python | `0001_initial.py` 2,954; `routers/overlay.py` 1,397; `capture/ingest.py` 1,089; `routers/governance.py` 1,010; `routers/core.py` 982; `routers/presentations.py` 874; `routers/item_extras.py` 708; `exports/pptx_builder.py` 697; `governance/habits.py` 640; `routers/query.py` 599 |
| Largest TS | `Present.tsx` 578; `Policies.tsx` 534; `Templates.tsx` 516; `Layouts.tsx` 402; `lib/governance.ts` 334; `Jira.tsx` 332; `ReviewQueue.tsx` 292; `Views.tsx` 261; `Violations.tsx` 252; `Weights.tsx` 246 |
| TODO/FIXME | 0 · `type: ignore` 180 (mostly tests) · `: any`/`as any` 7 (all in `api/http.ts`) · `Row = Record<string, any>` 233 refs |
| Web bundle | 732 kB: one `index-*.js` 719.6 kB (231.7 kB gzip), CSS 11.6 kB |
| API | 272 operations; OpenAPI and `schema.d.ts` regenerate identically |
| Misc | 2 import cycles; 123 function‑level imports; 24 `except Exception` (0 bare, 0 silent); docstrings 142/323 public functions (44 %); 18 duplicate 8‑line blocks; 40 ADRs |

## What is good — keep as‑is
1. `UnitOfWork` + `before_flush` guard + transactional outbox (`db/uow.py:53-77`, `events/outbox.py:13-30`): rule 3 is a runtime invariant.
2. Capture ingestion shape (`capture/ingest.py:1052-1089`): one transaction per message, IntegrityError → retry → deduplicated, failing message never fails the batch, inline Jira push after commit and time‑bounded.
3. Snapshot/query data access (`snapshot/assembler.py:126-180`, `query/service.py:129-146`): `IN`‑chunked aggregates, internal paging with hard cap, per‑viewer cache invalidated by propagation, Chromium renders bounded by semaphore with 503 `render_busy`.
4. Security fundamentals: fail‑fast settings, HttpOnly+SameSite cookies with double‑submit CSRF, constant‑time webhook HMAC, `_SAFE` path alphabet, scope ceiling, gated `/metrics`, NUL‑byte middleware, pip‑audit/bandit/pnpm audit in CI, non‑root read‑only Helm pods.
5. Gateway guardrails (`tt_llm/guards.py`, `agent/registry.py:60-75`, `agent/loop.py:63-95`, `prompts/`): untrusted text wrapped and labelled, grounding against invented keys, truncated tool results, step/tool budgets, versioned prompts.
Also: 0 TODOs, mypy `--strict` clean across 222 files, behaviour‑first tests (9 monkeypatch sites), 40 ADRs.

## Refactoring recommendations (value ÷ effort)
1. Index migration + FK‑index test (risk 3) — S. 2. Hermetic test settings + concurrency fixture (2, 10) — S/M. 3. Retry on event‑driven jobs + failed‑job metric (5) — S. 4. Proxy‑aware rate limiting (1) — S. 5. `ws_today()` + ruff ban (8) — S. 6. Docker hardening (9) — S. 7. `React.lazy` + ErrorBoundary (12) — S. 8. Domain exception hierarchy (7) — M. 9. Extract orchestration from routers; split by resource (6) — M. 10. Startup seeding of builtins; single `commit_bookkeeping` allow‑list (4) — M. 11. Typed API wrappers (11) — M. 12. Reserve‑then‑settle gateway budget with `Decimal` (14) — S/M. 13. DB enums/CHECKs + explicit ON DELETE (15) — M. 14. OTel instrumentors + shared engine for periodic jobs — S/M. 15. Deduplicate: tag‑name lookup (`overlay/weights.py` vs `jira/sync.py`), classification→dimension‑value join (`rollup.py` vs `weights.py`), assignee‑handle resolution (`routers/core.py` vs `routers/llm.py`), template‑gallery select (`presentations.py` vs `presentation_templates.py`) — S.

Not verified: server coverage re‑measurement, perf suites, real‑model goldens, Playwright (web/presentation), Tauri/mobile, Helm/kind.


---

# Part 2 — UX review by use

Reviewed on a live local stack (Postgres 16 + API + worker + Vite web + built deck bundle), driven by Playwright/Chromium at 1440×900 and 390×844 with axe-core 4.14 on every screen, plus the `tt` CLI as @sara. No LLM gateway was available, so every assisted path ran on the deterministic parser (the spec's documented fallback). A representative subset of the 142 screenshots (27 files, downscaled where large) plus the CLI transcript is committed under `docs/reviews/2026-10-25-ux/`; linked filenames open those. Unlinked filenames refer to working screenshots that were not committed.

## (a) Executive summary and grades

The build is remarkably complete in breadth — every surface in the UX doc exists and most of the plumbing works (filter grammar + URL round trip, drill-down breadcrumb, 409 conflict dialog, pipeline WIP breach, deck keyboard map, print/PDF/PPTX on the server, plan-first ERP deck, restricted wellbeing). What hurts users are a handful of broken "last-mile" interactions (every export button in the web UI fails, the builder's live preview is blank because the deck blocks framing, destructive delete without confirmation), raw internal IDs leaking into headings and chips, a review queue that cannot offer one-click suggestions without an LLM and that routes a reporter's own lines back to herself, and a web shell that does not adapt below ~1000 px.

| Surface | Grade | One sentence |
|---|---|---|
| CLI / reporter | B | One line in ≈1 s with zero forms and a readable card, but a stray free-text line silently becomes a note on whatever is active, and the "sorted by your manager" cue appears even when the item is already fully resolved. |
| Review queue | C | Rows resolve quickly and learn into the glossary, but with no suggestions there is no one-click default, Enter does nothing, no row shows who/when, there is no undo on resolve, and project owners review their own captures. |
| Setup editors | C+ | Everything exists as clean tables and forms (add project = name/key/process, risk drag-link works), but the tree, calendar, drag-reorder, chip popover and dry-run panel promised in §3.3 are absent or degraded. |
| Views | B | Seven layouts, chips with negation, shareable URLs, matrix drill with Backspace and card anatomy all work; CSV export silently fails, Gantt has no axis, calendar shows bare keys, and only matrix cells drill. |
| Presentation / deck | B− | The deck itself is the best-looking surface (keyboard, grid, notes, deep links, swipe, dark, print, drill), but the builder preview is a broken iframe, exports throw, share is a dead end, KPI tiles don't drill, slides overflow instead of auto-fitting. |
| Governance | B | Policy templates without CEL, dry run with hit count, grouped violations with waive-reason dialog and key hints, restricted wellbeing are all real; habits/digest leak UUIDs and viewers see every nav entry only to get 403s. |
| Overall | C+ | Strong architecture and feature coverage; needs a focused polish pass on feedback, naming, responsiveness and a few blocking bugs before a non-technical manager would trust it. |

## (b) Top 15 UX issues (ranked by user impact)

| # | Issue | Evidence | What happens / why it hurts | Recommendation | Effort |
|---|---|---|---|---|---|
| 1 | **Every download button in the web UI fails** (Views → Export CSV; Present → HTML/PDF/PPTX/PNG/CSV; "Download pack") | `[160-views-export-csv-silent.png](2026-10-25-ux/160-views-export-csv-silent.png)`, `[94-present-after-exports.png](2026-10-25-ux/94-present-after-exports.png)`, notes-08 | `api.postBlob` calls `response.clone().blob()` after openapi-fetch already consumed the body → `TypeError: Response body is already used`. Views swallows it (blank toast, nothing happens); Present shows the raw TypeError as a toast. Server endpoints work (`render?format=pdf` → 137 KB PDF in 1.3 s). | Return `response.blob()` without `clone()`; add a "Preparing PDF…" pending toast and a success toast with the stamp. | S |
| 2 | **Builder live preview is a dead iframe** | `[91-present-builder.png](2026-10-25-ux/91-present-builder.png)`, `[162-builder-preview-iframe.png](2026-10-25-ux/162-builder-preview-iframe.png)`, console: *Framing violates CSP frame-ancestors 'none'* | The deck shell sets `frame-ancestors 'none'`, so the builder's own `<iframe src=/p/acme/{id}?slide=n>` renders "127.0.0.1 refused to connect". The manager edits slides blind. | Allow `frame-ancestors 'self'` (or embed the renderer module as P7 suggested); show a skeleton while loading. | S |
| 3 | **Reporter's own captures land in her own review queue** | `[137-review-sara-own-queue.png](2026-10-25-ux/137-review-sara-own-queue.png)`, API: @mahmoud queue = 0 after @sara's lines | `review_owner()` prefers the project owner; the seed makes @sara (reporter persona) owner of WEB, so her unresolved tags/people go to herself — contradicting §2.2 "lands in the manager's review queue, not the reporter's". Also a line with *no* project mention goes to Inbox with no review row at all (§3.2 "Unclassified" never appears). | Route to the primary line manager first; create an `unclassified_project` row whenever an item is created in an Inbox; fix the seed so @sara is a member, not owner. | M |
| 4 | **Free text with an active timer becomes a note on the active item** | cli-transcript: `tt say "refactor the ingestion retry p:ingestion #retry-logic"` → *note* on WEB-114; `tt say "frobnicate the glorp"` → ✓ WEB-114 | The card shows no Task row for the target, then the success line names a different item. The UX doc's own §3.2 example line would have been swallowed. Surprising, data-corrupting default. | Default to `create` when the line has a noun phrase and no verb; always show the target item in the card; ask (`ask` cue) when ambiguous. | M |
| 5 | **Raw IDs instead of names** (habits team headings, drawer "reported by 01a1723a", provenance "rule · 01a1723a-eede-…", digest "stage.moved · 01a17242-…") | `[10-habits.png](2026-10-25-ux/10-habits.png)`, `[28-item-drawer-overview.png](2026-10-25-ux/28-item-drawer-overview.png)`, `[29-item-drawer-provenance-hover.png](2026-10-25-ux/29-item-drawer-provenance-hover.png)`, `10-digest.png` | Recognition fails; the provenance chip promised as `set by @lead-b · 2d ago · inherited from Product X` reads as a UUID and a glyph "ƒ". | Resolve person/team/rule/item names server-side in these payloads; chip text `set by @sara · 2 d ago`, hover for detail. | S–M |
| 6 | **"Delete presentation" has no confirmation and dumps you on the review queue** | notes-08: after click `url=/w/acme/review`, no dialog | One mis-click destroys a deck; the redirect loses context. | Confirm dialog with title; redirect to the Presentations list with an undo toast. | S |
| 7 | **No responsive layout below ~1000 px** | `[80-mobile-review.png](2026-10-25-ux/80-mobile-review.png)`, `[80-mobile-pipeline.png](2026-10-25-ux/80-mobile-pipeline.png)`, `112-builder-mobile.png` (scrollWidth 543 at 390 viewport) | Fixed 190 px sidebar + fixed content; horizontal page scroll on every console screen; no hamburger. Deck itself adapts (`174-deck-mobile-slide1.png`). | Collapse sidenav to a drawer < 900 px; make toolbar wrap; min-width 0 on grid children. | M |
| 8 | **Review queue has no one-click default and Enter is inert** | `[10-review.png](2026-10-25-ux/10-review.png)`, `[21-review-after-enter.png](2026-10-25-ux/21-review-after-enter.png)` | Without LLM confidence every row shows `other…` + Ignore; `Confirm all suggested (0)`; the heading lacks the count; rows lack reporter/time (§3.2 mock shows `@ali today 10:12`). Keyboard `j/k` moves a highlight but `Enter` on a row with no suggestion does nothing and gives no hint. | Add a deterministic suggestion (project of the matched item / glossary hit / most-used project for the reporter) so the primary button exists; show `@reporter · 2h ago`; Enter opens the `other…` picker when no suggestion. | M |
| 9 | **No undo after resolving a review row; duplicate toasts** | `23-review-resolved-toast.png` | Plan 5.3 promised a 3 s undo and a "Don't learn" toast; instead two identical "Next time 'ingestion' → DATA" toasts and no way back. Inline edits do get `Undo` (`31-item-drawer-inline-edit-toast.png`) — also duplicated. | Register undo for resolve; dedupe toasts (StrictMode double-fire). | S |
| 10 | **Setup editors miss the interaction model in §3.3** | `10-setup-projects.png` (no tree, no drag), `[42-setup-process-chip-open.png](2026-10-25-ux/42-setup-process-chip-open.png)` (click does nothing; ←→ buttons only), `10-setup-planning.png` (table, no calendar), `[47-setup-risks-after-drag.png](2026-10-25-ux/47-setup-risks-after-drag.png)` (heat-map has no P axis labels, cell click no-op) | Managers get flat CRUD tables where the design promised product→project→epic tree, stage chip popover, month calendar, heat-map drill. | Short term: label heat-map axes, make heat cell filter the register, make stage chip open an inline editor; medium: tree + calendar. | M–L |
| 11 | **Gantt is unreadable**: no time axis, no today line, no labels on bars, no milestones, no zoom, 60+ rows | `[10-views-gantt.png](2026-10-25-ux/10-views-gantt.png)` | A viewer cannot answer "when" from it; the prototype's Plan section does show an axis (`[150-prototype-light.png](2026-10-25-ux/150-prototype-light.png)`). | Reuse the deck's Plan renderer (axis, today, diamonds, legend); default rows = epics; zoom day/week/month. | M |
| 12 | **Viewer sees every nav entry, then gets 403s or empty screens** | `33-ali-review-forbidden.png`, `141-ali-policies.png`, `141-ali-setup.png` | @ali (contributor) sees Setup, Review queue, Policies… each loading to an empty page with console 403s; no message. | Hide nav entries by `me.can.*`; show "You don't have access" with who to ask. | S |
| 13 | **Share link is a dead end** | `161-present-share-link-result.png` | "Create read-only link" → toast *external sharing is disabled for this workspace (settings.external_share) (request …)* plus the postBlob TypeError. No path for an admin to enable it in the UI. | Hide/disable the button with a reason tooltip; add the setting to Setup; show the copied URL inline on success. | S |
| 14 | **Deck numbers don't all drill; slides overflow** | `190-deck-kpi-drill.png` (KPI click = nothing), `[100-deck-slide1.png](2026-10-25-ux/100-deck-slide1.png)` (4th project card clipped), `[109-deck-dark-slide2.png](2026-10-25-ux/109-deck-dark-slide2.png)` (KPI slide 70 % empty), drill rows not clickable (`192-deck-item-drawer.png`) | §5 "each tile drills down", P7 "fit: shrink|paginate" and "opens the item drawer inside the deck" are not met; table toggle on charts absent. | Make KPI tiles buttons that open the drill list; implement shrink/paginate with "(cont.)"; make drill rows open the deck item drawer; add table toggle to explorer bars. | M |
| 15 | **CLI cues contradict reality** | cli-transcript: `tt blocked WEB-114 …` → Task ✓ matched, Project "— will be sorted by your manager"; `tt today` → "no daily log yet (it is drafted at 17:30 or when you log work)" right after logging 45 min; `--yes` documented but non-TTY sends without asking | The failure copy from §2.6 is shown when nothing failed; the daily-log promise is false (0 rows in `daily_logs`). | Infer project from the matched item; show "LLM offline — parsed locally" only when the gateway was tried; draft the daily log on first time entry or reword. | S |

Also noted (lower impact): weight-scheme selector lists "default" twice and there is no second scheme to compare (`65-views-pipeline-weight-switch.png`); pipeline column headers are not links (only matrix cells drill); `?f=garbage` is silently ignored; calendar shows bare keys with no titles/month nav (`10-views-calendar.png`); keys for new items start at `WEB-1` beside seeded `WEB-131`; policy template list has no selected state (`130-policy-new-templates.png`); category keys (`stuck_in_stage`) used as headings in Violations; 409 dialog says "Changed by someone else" instead of who and when (`163-409-conflict-dialog.png`); Projects slide omits the plan-only ERP project; `@nobody` is stripped from the item title ("the dashboard export with").

## (c) Behaviour checklist vs `team_tracker_ux.md`

| § | Behaviour | Status | Evidence |
|---|---|---|---|
| 2.2 | Type naturally; syntax optional | Met | cli-transcript (plain line parsed to start+estimate) |
| 2.2 | Live parse with ~300 ms debounce | Could not test | CLI is one-shot; desktop palette not run |
| 2.2 | Confidence cue per row (✓ / % / suggested / ask) | Partially | ✓ matched, new, suggested shown; no % or "ask" without LLM |
| 2.2 | One keystroke to send | Met | sends immediately (non-TTY) |
| 2.2 | Tab to correct / edit a row | Partially | `tt edit`/`tt form --from` + outbox JSON; no row-level edit in CLI card; edited minutes 120→90 did land as a 90-min TimeEntry |
| 2.2 | "Ask" rows never block; land in manager queue | Partially | never blocks; but no-project lines create no review row and WEB owner = reporter (issue 3) |
| 2.2 | Multi-item lines → stacked cards | Met | 3 cards, "minutes applied to the first item" |
| 2.2 | Running timer; start auto-pauses previous | Met | `tt now` shows ▶ WEB-114 after switching |
| 2.2 | Offline-first outbox, idempotent | Met (outbox) / Could not test (offline) | `tt outbox`, sent/ moved |
| 2.3 | Verbs start/done/pause/blocked/time/plan/estimate/note/create | Met | transcript covers all but pause/evidence |
| 2.4 | ContextPack projects/aliases/glossary/rules | Partially | pack served; glossary empty in seed so §7.1 "the pipeline → DATA" fails |
| 2.5 | Jira chip / `--jira` | Could not test | no Jira configured |
| 2.6 | Empty state "What are you working on?" | Missing (CLI) | `tt say ""` → "nothing to send" |
| 2.6 | First-run sample lines | Missing | none in CLI |
| 2.6 | LLM unavailable → still works, "will be sorted by your manager" | Partially | works; cue shown even when project is resolvable (issue 15); no offer to switch to form |
| 2.6 | End-of-day "Confirm today's log?" | Missing / Could not test | no daily log created after logging work |
| 3.1 | IA: Setup / Review / Policies / Weights | Met | `10-setup-*.png`, nav |
| 3.2 | Flat list, one-click default = suggestion | Partially | no suggestions → `other…` select (issue 8) |
| 3.2 | Confirm writes classification + glossary learning | Met | `27-glossary-after-learning.png` ("ingestion → DATA, learned, 1 hit") |
| 3.2 | Bulk assign / Confirm all suggested | Met | `24`, `25-review-after-bulk.png` |
| 3.2 | Keyboard j/k/Enter/x | Partially | j/k/x work; Enter inert without suggestion |
| 3.2 | Queue scoped per manager, never twice | Met | mahmoud 9 rows, youssef 11, sara 2, disjoint |
| 3.2 | Empty state "You're all caught up" | Met | `26-review-empty.png` |
| 3.3 | Projects tree, drag-to-reparent, add asks only name/key/process | Partially | add = 3 fields met; no tree/drag |
| 3.3 | Process chips, drag reorder, chip popover, live mini-pipeline | Partially | ←→ buttons reorder and counts update; no popover/drag |
| 3.3 | Dimension editor (name, tree/flat, single/multi), CSV paste | Partially | form present; applies-to and CSV paste not found |
| 3.3 | Risks register + 5×5 heat-map; drag onto tree creates RiskLink | Met (link) / Partially (map) | `[47-setup-risks-after-drag.png](2026-10-25-ux/47-setup-risks-after-drag.png)`: "Linked DATA-R3 to WEB-120; children inherit it"; map unlabeled, no drill |
| 3.3 | Translator rules dry-run "would have changed N" | Could not test | honest "Dry run unavailable: the LLM gateway is not configured" |
| 3.3 | Bulk priority/weight with propagation preview | Could not test | no multi-select in views found |
| 3.4 | Per-field overlay events; 409 → diff + revert | Met | `163-409-conflict-dialog.png` Keep mine / Take theirs / Revert |
| 3.4 | Provenance chip `set by · when · inherited from` | Partially | chip exists but shows rule UUID (issue 5) |
| 4.1 | Persistent top bar: scope ▾, filters, layout, weight ▾, save view | Met | `10-views-pipeline.png`; scope → `?scope=team:squad-b` |
| 4.1 | Chips with typeahead over DimensionValues, negation, group-by | Partially | select-based (not typeahead); negation ¬ works; group-by select |
| 4.2 | Layouts list/board/pipeline/matrix/gantt/calendar/now/summary | Met | all tabs render; summary = Present |
| 4.2 | Card anatomy (key·prio·weight·risk / title / assignee·age·time / tags·milestone) | Met (minus milestone) | `10-views-pipeline.png` |
| 4.2 | Stage-age amber/red; WIP breach border | Met | Build 14/4 red border, 20d/10 red |
| 4.3 | Every number is a link | Partially | matrix cells yes; pipeline column counts, KPI tiles no |
| 4.3 | Breadcrumb + Backspace, chips update | Met | `63`, `64-views-*.png` |
| 4 | Views are shareable URLs respecting ACL | Met | `62-views-url-roundtrip-ali.png` (37 rows for @ali vs 63) |
| 5 | Header strip, KPI row, project cards, explorer, pipeline, radar, gantt, team, violations | Met | deck slides 1–9 + `[107-deck-page-mode.png](2026-10-25-ux/107-deck-page-mode.png)` |
| 5 | LLM summary per project with fallback | Met (fallback) | deterministic "4/11 items done (36 %)…" |
| 5 | Print-friendly A4 landscape | Met | `deck-print.pdf`, `108-deck-print-emulated.png` |
| 5 | Keyboard navigable | Met | → ← G N F End Esc, focus rings 2 px |
| 5 | Every chart has a table toggle | Missing | 0 toggles found |
| 5 | No external calls; dark/light by prefers-color-scheme | Met | 0 external requests logged; `[109-deck-dark-slide2.png](2026-10-25-ux/109-deck-dark-slide2.png)` |
| 5 | Page mode pixel-comparable to prototype | Partially | same sections/tokens, different section titles and order (`107` vs `[150-prototype-light.png](2026-10-25-ux/150-prototype-light.png)`) |
| 6 | Scope selector lists only readable scopes | Met | @sara sees WEB/squad-b/@sara/@ali only |
| 6 | Wellbeing restricted except subject/line manager | Met | `[136-habits-sara-restricted.png](2026-10-25-ux/136-habits-sara-restricted.png)` |
| 6 | Inbox items never in team views | Met | INBOX-* not in pipeline (DATA-1 appears only after reassignment) |
| 6 | Exports stamped with viewer and scope | Met (server) / Missing (UI) | CSV header "# Exported by @mahmoud · scope all visible · at …"; deck footer stamp; but UI download broken |
| 7.1 | Reporter morning flow in ~6 s | Met | 1.1 s, zero forms |
| 7.2 | Manager: confirm match, drag risk, set weight ≤ 6 clicks | Partially | confirm (1 select), drag (1 drag), weight not inline-editable in drawer (read-only "2.0000") |
| 7.3 | Senior manager: summaries → pipeline test → item → team×stage → share URL | Partially | deck drill to list works; drill row → item drawer does not; explorer fixed to team×stage; share disabled |

## (d) Accessibility

axe-core 4.14, default ruleset, 1440×900, @mahmoud (counts are violating nodes):

| Screen | Critical | Serious | Moderate | Minor | Main rule |
|---|---|---|---|---|---|
| Login | 0 | 0 | 0 | 0 | — |
| Review queue | 0 | 9 | 0 | 0 | `nested-interactive`: `<li role=option>` wrapping checkbox/select/buttons |
| Setup (each of 10) | 0 | 0 | 1 | 0–1 | `heading-order` (h3 after h1), `empty-table-header` (identity, glossary) |
| Views list/gantt/calendar/now | 0 | 0 | 1 | 0 | `page-has-heading-one` (views have no h1) |
| Views board | 0 | 12 | 1 | 0 | `nested-interactive` on dnd-kit cards |
| Views pipeline | 0 | 56 | 1 | 0 | same |
| Views matrix | 0 | 2 | 1 | 0 | `color-contrast` white text on `--seq6` cells |
| Present / Plan / Violations / Habits / Performance / Insights | 0 | 0 | 0 | 0 | — |
| Policies / Weights / Digest | 0 | 0 | 0–1 | 1 | `empty-table-header`, `heading-order` |
| Deck (slide view) | 0 | 13 | 2 | 0 | `color-contrast` ×12 on "as of"/muted spans, `aria-progressbar-name`, `region` |

Keyboard: Tab order in the console is logical (workspace → scope → bell → nav → content) and every focused element shows a 2 px solid outline (`22-review-tab-focus.png`); review `j/k/x` work but the highlighted row is not the focused element (`activeElement` stays `body`), so screen readers get nothing; `aria-selected` on `role=option` is correct. Deck: Tab reaches drill links then Prev/Next/Overview/Notes/Fullscreen with visible rings (`[170-deck-focus-ring.png](2026-10-25-ux/170-deck-focus-ring.png)`); KPI tiles are not focusable; drill dialog close works with Esc; `prefers-reduced-motion` → transition 0 s (0.25 s otherwise). Board cards announce dnd-kit live text ("Draggable item DATA-118 was moved over droppable area todo") but focus ring is browser default 1 px auto. Dark mode is implemented everywhere via tokens (`70-dark-*.png`); contrast of secondary text in the deck fails AA. Touch targets on mobile console are fine in size but the layout overflows.

## (e) What works well

1. **The deck**: one slide per screen, `→ ← Home End G N F Esc`, `?slide=n` deep links, swipe, progress bar + counter, dark/light, print to A4 with footer stamp, drill lists inside the deck, zero external requests — and it looks like the prototype (`100`, `102`, `109`, `171`).
2. **Filter/URL model**: chips with negation serialise to `?f=` and `?d=`; a viewer opening the URL sees the same chips with ACL applied; matrix drill → breadcrumb → Backspace is exactly §4.3 (`61`–`64`).
3. **Plan-first ERP**: roadmap editor with dates, timeline bars and status, milestones, "Present this plan" → an 8-slide plan deck with sensible empty states ("0 of 9 done", "—" for hours) (`120`–`122`).
4. **Governance**: policy templates without CEL, "Example hit", dry run with 25 hits before activation, violations grouped by severity/category with reason-required waive dialog and `a/r/w/f` keys, restricted wellbeing copy (`131`–`136`).
5. **Conflict handling and learning loop**: 409 dialog with theirs/yours/Keep/Take/Revert; resolving a queue row immediately writes a learned glossary entry with hit counts and "Promote to manual" (`163`, `27`).

## (f) Quick wins vs larger redesigns

Quick wins (≤ 1 day each): fix `postBlob` clone (#1); CSP `frame-ancestors 'self'` for the preview (#2); confirm + undo on delete presentation (#6); resolve names for provenance/habits/digest/drawer header (#5); hide nav entries the user cannot use and add an access message (#12); dedupe toasts and add undo on resolve (#9); disable share button with reason (#13); label heat-map P axis and make cells filter (#10 part); show reporter + relative time on queue rows; make Enter open the picker when no suggestion; remove duplicate "default" weight option; infer project from matched item in CLI card and fix `tt today` copy (#15); add `h1` to Views pages and name the deck progressbar.

Larger (multi-day): responsive shell (#7); deterministic suggestion engine for the queue + line-manager routing + unclassified rows for Inbox items (#3, #8); capture intent model so bare noun phrases create items instead of notes (#4); Gantt renderer with axis/today/milestones/zoom (#11); projects tree + process chip popover + milestone calendar (#10); deck auto-fit/pagination, KPI drill, drill→item drawer, chart table toggles (#14); CLI first-run/empty-state and end-of-day daily-log flow (§2.6).

## (g) Commands used to boot the stack

```
# Postgres (system cluster was down; it started after removing a stale pid)
pg_ctlcluster 16 main start
su postgres -c "psql -c \"CREATE USER tt WITH PASSWORD 'tt' CREATEDB SUPERUSER\" -c \"CREATE DATABASE tt OWNER tt\""
su postgres -c "psql -d tt -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm' -c 'CREATE EXTENSION IF NOT EXISTS ltree' -c 'CREATE EXTENSION IF NOT EXISTS btree_gist'"
# Server
cd <checkout> && cp .env.example .env && uv sync --all-packages
set -a && . ./.env && set +a
uv run tt-admin migrate            # "migrations applied; procrastinate schema applied"
uv run tt-admin seed demo          # "seeded 49 work items (+ the plan-only project ERP)"
uv run uvicorn tt_server.main:create_app --factory --host 127.0.0.1 --port 8000 &
uv run python -m tt_worker &       # started later; review rows are created synchronously anyway
# Web + deck
pnpm -C <checkout> install --frozen-lockfile        # (plain `pnpm install` from an agent shell failed: cwd reset)
pnpm -C <checkout> --filter @tt/web dev -- --host 127.0.0.1 &     # :5173, proxies /api,/p/,/auth to :8000
pnpm -C <checkout> --filter @tt/presentation build  # dist/presentation.html 58.7 KB
# Dev auth: POST /auth/dev/login {workspace:"acme",handle:"@mahmoud"} sets the session cookie (TT_DEV_AUTH=1); API calls used X-Debug-Actor
# CLI (as @sara)
TOKEN=$(curl -s -X POST localhost:8000/auth/tokens -H 'X-Debug-Actor: @sara' -H 'content-type: application/json' -d '{"workspace":"acme","name":"ux-cli","scopes":["capture"]}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
HOME=<scratch>/ttcli-home uv run tt login --url http://localhost:8000 --workspace acme --handle @sara --token "$TOKEN" --time-zone Asia/Riyadh
# Playwright: Playwright 1.64.1 wanted chromium-1248; /opt/pw-browsers only has 1243, so scripts launch with
#   executablePath=/opt/pw-browsers/chromium-1243/chrome-linux64/chrome (no `playwright install` run)
# axe: node_modules/.pnpm/axe-core@4.14.0/node_modules/axe-core/axe.min.js injected (@axe-core/playwright not installed)
# LLM gateway: not run (no model); all assisted paths used the deterministic parser; "Generate with AI" → 503 "no LLM gateway is configured", "Download pack" → 403
```
Failures: `pnpm install` from the agent shell (cwd reset — fixed with `-C`); Playwright browser version mismatch (worked around); `tt form` interactive mode not driven (used `--answers`); real touch swipe simulated with synthetic TouchEvents on `.viewport`.

## Screenshot index (SHOTS/…)

00-login.png login page with Development sign-in · [10-review.png](2026-10-25-ux/10-review.png) review queue, 9 rows, no suggestions · 10-setup-projects.png flat project/product tables · 10-setup-process.png stage chips · 10-setup-dimensions.png · 10-setup-people.png · 10-setup-identity.png AD group mapping · 10-setup-planning.png milestones table (no calendar) · 10-setup-risks.png · 10-setup-glossary.png · 10-setup-rules.png · 10-setup-manual.png · 10-views-default/list.png list with 63 items · 10-views-board.png · 10-views-pipeline.png WIP breach + stage ages · 10-views-matrix.png heat-map · [10-views-gantt.png](2026-10-25-ux/10-views-gantt.png) bars without axis · 10-views-calendar.png month grid with keys · 10-views-now.png · 10-present.png presentations landing · 10-plan.png plan list · 10-policies.png · 10-violations.png grouped violations · [10-habits.png](2026-10-25-ux/10-habits.png) habits with UUID team headings · 10-performance.png · 10-digest.png digest with UUID events · 10-insights.png · 10-weights.png · 20-review-keyboard-cursor.png j/k highlight · [21-review-after-enter.png](2026-10-25-ux/21-review-after-enter.png) Enter no-op · 22-review-tab-focus.png focus ring · 23-review-resolved-toast.png duplicate learn toasts · 24-review-bulk-selected.png · 25-review-after-bulk.png · 26-review-empty.png "You're all caught up" · 27-glossary-after-learning.png learned entries · [28-item-drawer-overview.png](2026-10-25-ux/28-item-drawer-overview.png) drawer with raw reporter id · [29-item-drawer-provenance-hover.png](2026-10-25-ux/29-item-drawer-provenance-hover.png) UUID provenance · 30-item-drawer-activity.png raw JSON activity · 31-item-drawer-inline-edit-toast.png undo toast ×2 · 32-item-drawer-409-conflict.png (no dialog that run) · 33-ali-review-forbidden.png viewer gets empty queue · 40-setup-projects-empty-submit.png native validation · 41-setup-projects-added.png UXR added · [42-setup-process-chip-open.png](2026-10-25-ux/42-setup-process-chip-open.png) chip click no popover · 43-setup-process-after-move.png reorder · 44-setup-dimensions.png · 45-setup-people-detail.png tables + reporting-line form · 46-setup-identity-test.png · [47-setup-risks-after-drag.png](2026-10-25-ux/47-setup-risks-after-drag.png) risk linked toast · 48-setup-rules-dryrun.png dry run unavailable · 49-setup-manual.png required fields + export · 50-command-palette.png ⌘K result · 51-scope-squad-b-pipeline.png scope filter · 60-views-filter-picker.png · [61-views-filter-negated.png](2026-10-25-ux/61-views-filter-negated.png) ¬ chip · 62-views-url-roundtrip-ali.png same URL as viewer · [63-views-drill-breadcrumb.png](2026-10-25-ux/63-views-drill-breadcrumb.png) · 64-views-after-backspace.png · 65-views-pipeline-weight-switch.png duplicate default · 66-views-card-drawer.png · 67-views-bad-url.png · 70-dark-review/pipeline/matrix/present.png dark theme · 80-mobile-*.png console overflow at 390 px · 90-present-generate-ai-nogateway.png 503 copy · [91-present-builder.png](2026-10-25-ux/91-present-builder.png) builder with broken preview · 92-present-builder-slide-editor.png · 93-present-share.png · [94-present-after-exports.png](2026-10-25-ux/94-present-after-exports.png) · 95-views-add-to-presentation-menu.png · 96-present-templates.png gallery · 97-present-template-editor.png · 98-present-list-with-decks.png · [100-deck-slide1.png](2026-10-25-ux/100-deck-slide1.png) projects slide (card clipped) · 101-deck-slide3.png · 102-deck-grid.png G overview · 103-deck-notes.png N pane · 104-deck-last.png · 105-deck-deeplink-slide4.png · [107-deck-page-mode.png](2026-10-25-ux/107-deck-page-mode.png) page mode · 108-deck-print-emulated.png · [109-deck-dark-slide2.png](2026-10-25-ux/109-deck-dark-slide2.png) KPI slide dark · 110/111-deck-mobile-*.png · 112-builder-mobile.png · [120-plan-erp-roadmap.png](2026-10-25-ux/120-plan-erp-roadmap.png) roadmap editor · 121-plan-erp-builder.png · 122-plan-erp-deck-slide1..4.png plan deck · 130-policy-new-templates.png · [131-policy-new-form.png](2026-10-25-ux/131-policy-new-form.png) · [132-policy-dryrun.png](2026-10-25-ux/132-policy-dryrun.png) 25 hits · 133-policy-cel-error.png · 134-violations-waive-dialog.png · 135-habits-mahmoud.png · [136-habits-sara-restricted.png](2026-10-25-ux/136-habits-sara-restricted.png) · [137-review-sara-own-queue.png](2026-10-25-ux/137-review-sara-own-queue.png) · 138-notifications-bell.png "No notifications." · 139-insights-regenerate.png · 140-policies-list.png · 141-ali-*.png viewer screens · 142-unauth-deeplink.png redirect to login · 143-login-error.png · 150-prototype-light/dark.png reference prototype · [160-views-export-csv-silent.png](2026-10-25-ux/160-views-export-csv-silent.png) · 161-present-share-link-result.png · [162-builder-preview-iframe.png](2026-10-25-ux/162-builder-preview-iframe.png) refused to connect · 163-409-conflict-dialog.png · 166/167-deck-mobile-*.png · 168-review-sara-queue-kinds.png · [170-deck-focus-ring.png](2026-10-25-ux/170-deck-focus-ring.png) · 171-deck-drill-list.png · 174–176-deck-mobile-*.png · 177-deck-kpis.png · 178-deck-radar.png · 179-deck-people.png · 180-deck-violations.png · 181-deck-ali-people-restricted.png (deck deleted by then → "unavailable") · 190-deck-kpi-drill.png no drill · 191-deck-pipeline-drill.png column drill · 192-deck-item-drawer.png row not clickable · 193-board-card-focus.png. Files: cli-transcript.txt, deck-print.pdf, items.csv (stamped), notes-*.txt, tour-1440.json.

---

# Resolution (Phases 13–15, implementation line `feat/phase-14-server`)

Status of the ranked findings after the remediation phase (plan 15) and what changed around it. "Fixed" means a test guards it.

## Technical risks
| # | Finding | Status | Where |
|---|---|---|---|
| 1 | Proxy-blind rate limiter | Fixed | trusted-proxy client IP in `ratelimit.py`; ADR-0041 |
| 2 | `Settings` reads `.env` in tests | Fixed | hermetic fixture, `tests/unit/test_hermetic.py` |
| 3 | 60 FK columns without indexes | Fixed | migration 0011; `tests/unit/test_fk_indexes.py` |
| 4 | UoW bypassed on read paths that write | Fixed | `commit_derived`; `tests/unit/test_architecture.py` (only the UoW commits) |
| 5 | Event jobs without retry | Fixed | `jobs/retry.py`, ADR-0044; `tests/unit/test_job_retries.py` |
| 6 | Orchestration in fat routers | Mostly fixed | overlay/core/governance/item_extras/query split into per-concern routers with services under `domain/`; routers ≤ 560 lines. Still over 400: `presentations.py` (556), `domain/overlay/review.py`, `governance/habits.py`, `domain/capture/ingest.py`; not extracted: `access`, `jira`, `console`, `client`, `identity`, `webauth` |
| 7 | Domain depends on the API layer | Fixed | typed `domain/errors.py`, ADR-0047; architecture test |
| 8 | Server-local `date.today()` | Fixed | `ws_today`, `clock.py`; ruff DTZ ban |
| 9 | Container images | Fixed | multi-stage, non-root, pinned `uv`; `tests/unit/test_images.py` |
| 10 | Harness vs concurrency; suite speed | Fixed | `committed_client`, per-worker DB templates; full suite ≈ 14 min under xdist (was ≈ 80) |
| 11 | Frontend type erasure | Fixed | typed wrapper modules in `apps/web/src/api/`, `Row` used 8 times (limit 10) |
| 12 | No ErrorBoundary / code-splitting | Fixed | `ErrorBoundary`, `React.lazy` route groups |
| 13 | CSP `'unsafe-inline'` | Fixed | per-response nonce, `csp.py`, ADR-0046 |
| 14 | Gateway budget TOCTOU / float money | Fixed | reserve-then-settle with `Decimal`, ADR-0045 |
| 15 | Enum/cascade invariants only in app code | Fixed | migration 0012: CHECK on 96 enum columns, explicit `ON DELETE`; ADR-0049 |

## UX issues
1 downloads (fixed, `phase13.spec.ts`), 2 builder preview (fixed), 3 own captures in own queue (fixed, ADR-0042), 4 free text with an active timer (fixed, ADR-0043), 5 raw IDs (fixed, `domain/names.py`), 6 delete confirmation (fixed, `Confirm`), 7 responsive layout (a single breakpoint at 900 px; not re-audited on every screen), 8 review one-click default and Enter (fixed), 9 undo after resolve (fixed), 10 setup editors tree/drag (fixed, `Tree`, `ProjectTree`), 11 Gantt (fixed: axis, today line, labels, milestones, zoom), 12 nav gating (fixed), 13 share link dead end (fixed), 14 deck overflow (fixed: `fit` paginate/shrink and the KPI drill are guarded by `apps/presentation/e2e/fit-and-drill.spec.ts` and `src/kpi-drill.test.js`; each tile now says how its number relates to the list, e.g. "36 open violations on 31 items"), 15 CLI cues (fixed: end to end in `apps/cli/tests/test_first_run_and_cues.py`; first-run ghost examples, empty state and the end-of-day confirm nudge through `NotificationPort` are guarded there too).

## Added around the review
Phase 14 (management commands from the client, role modes, item merge/split: ADR-0050..0052, 0056) and Phase 15 (personal tracker with Google/Outlook/Teams calendars: ADR-0053..0055). Two defects of the remediation itself were found and fixed while integrating: the review-queue rows had become `group`s while the e2e specs still queried listbox options, and the Gantt scroll region and the confidence cue failed axe (focusable region, contrast).

## Not done / caveats
* Docstring coverage on public functions in `domain/` and `governance/` is below the 80 % target (62 % measured before about 50 were added).
* Playwright PDF/PNG exports and the TLS/perf tests depend on the machine (Chromium present, no CPU contention); they pass alone.
