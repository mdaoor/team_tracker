# Appendix D — Handover prompt templates

Use these to brief a fresh agent. Replace `<N>` and the bracketed parts. Keep the agent on **one phase per session**; a new session per phase avoids context drift.

## D.1 Kick‑off prompt for a phase
```
You are implementing Phase <N> of Team Tracker in the repository at <path/URL>, branch `phase/<N>-<slug>` (create from `main`).

Read, in this order, before writing code:
1. plans/README.md
2. plans/00-architecture-and-stack.md
3. plans/appendix-a-api-conventions.md, appendix-b-testing-and-dod.md, appendix-c-open-decisions-and-assumptions.md
4. plans/<phase file>  ← your task
5. The spec sections it cites in docs/spec/ (object model, UX, models.py). The object model is normative; models.py is normative for names/enums; the UX doc is normative for behaviour.
6. The "Handover notes" section of the previous phase file and docs/decisions/*.md.

Rules you must not break: the seven "Non-negotiable rules" in plans/README.md.

Work method:
- Start by writing the test list derived from the phase's Acceptance criteria into apps/server/tests/<phase>/TESTPLAN.md (or the web equivalent), then implement.
- Commit small, conventional-commit messages, run `make check` before each push.
- When the spec is silent, apply appendix C; if still unresolved, write an ADR using the template and continue. Do not stop to ask product questions; list them in the final report.
- Update docs/api/event-catalogue.md, docs/api/openapi.json, and plans/appendix-e-traceability-matrix.md as you go.

Done means: every acceptance criterion checked with a linked test, exit checklist complete, `make check` and `make e2e` green in CI, PR opened against main with the phase file linked and a summary of ADRs added and open questions.

Report back with: what was built (by module), test results (counts, coverage), deviations from the plan and why, ADRs written, open questions, and handover notes for Phase <N+1>.
```

## D.2 Resume prompt (agent continuing a partially done phase)
```
Phase <N> is partially implemented on branch `phase/<N>-<slug>`. Read plans/<phase file> and run `make check` to see the current state. Compare the acceptance criteria against existing tests (grep for `test_` names referenced in TESTPLAN.md). Finish the unchecked criteria in order of dependency, do not refactor completed work unless a test is red, and keep the commit history linear. Finish with the same report as D.1.
```

## D.3 Review prompt (independent reviewer for a phase PR)
```
Review PR <link> implementing Phase <N> against plans/<phase file> and the spec sections it cites. Check: (1) every acceptance criterion has a test that would fail if the behaviour regressed; (2) the seven non-negotiable rules in plans/README.md hold (especially capture-never-blocks and overlay-never-rewrites-capture, which have static guard tests); (3) field names/enums match docs/spec/team_tracker_models.py; (4) all emitted events are catalogued; (5) ACL applied on every new read path (phases ≥ 4). Report findings ranked by severity with file:line, and a verdict: merge / fix-then-merge / rework.
```

## D.4 Spec‑change prompt
```
A change to the design is requested: <describe>. Update docs/spec/* only via a new ADR in docs/decisions/ that states the change and the affected sections; then update the affected plans/*.md files and appendix-e. Do not edit code in this session.
```

## D.5 Remediation + new‑features prompt (after the 2026‑10‑24 review)
```
You are continuing the Team Tracker implementation in repository mdaoor/team_tracker.

Branches
- Plans and the review live on `main` (merge PR #3 first if it is still open; until then read them from branch `claude/optimistic-pasteur-lb1pvw`).
- The implementation lives on `claude/listed-plan-implementation-hygguz` (phases 0–9 built, HEAD 584eefb at review time). Work there: create `fix/review-2026-10-24` from it for work packages A–C, then one branch per feature package D–I, each merged by PR into the implementation branch.
- Never commit to `main` directly; never rewrite history on a branch you did not create.

Read before writing code, in this order
1. plans/README.md (the seven non‑negotiable rules; the "requirements added during planning" table)
2. plans/00-architecture-and-stack.md
3. docs/reviews/2026-10-24-implementation-review.md — your work list for packages A–C; the three slice reports at the end carry file:line evidence for every finding
4. plans/appendix-a-api-conventions.md, appendix-b-testing-and-dod.md, appendix-c-open-decisions-and-assumptions.md
5. The plan sections named under each package below, plus docs/spec/team_tracker_object_model.md where they cite it.
Plans are normative. Where the code already deviates in a way the review calls an "ADR candidate", keep the code and write the ADR (template in appendix C); where the review calls it a defect, fix the code.

Work method (applies to every package)
- Start each package by writing TESTPLAN.md in the relevant tests folder: one failing test per finding or acceptance criterion, then implement until green. A finding is closed only by a regression test that would fail without the fix.
- Run `make lint`, `make typecheck`, and the DB suite with `TT_TEST_DATABASE_URL` set (testcontainers is not available everywhere). Add a per‑test timeout (pytest‑timeout, 120 s) to the suite first; investigate the governance test that hangs idle‑in‑transaction on `INSERT INTO violations` and fix the cause.
- Add `--cov=tt_llm` and `apps/llm_gateway/tt_llm` to the mypy and coverage steps in `.github/workflows/ci.yml`.
- Small commits, conventional‑commit messages. Update docs/api/openapi.json, docs/api/event-catalogue.md, plans/appendix-e-traceability-matrix.md as you go. When the spec is silent, apply appendix C; if still unresolved, write an ADR and continue; never stop on a product question — list it in your final report.

PACKAGE A — security fixes (one PR, first)
Review findings 1–7. Required outcomes:
A1 Deck shell XSS: every path/query value injected into the shell HTML is JSON‑encoded with `<` escaped (as exports/presentation.py already does) or validated against ^[A-Za-z0-9_-]+$ → 404. Tests for `/p/t/{token}` and `/p/{ws}/{pid}` with `"`, `<`, `%22`, `%3C`.
A2 Token minting: `POST /auth/tokens` requires requested scopes ⊆ presenting token's scopes (session/debug actor may mint any); test "capture token cannot mint read/manage/admin".
A3 Review‑queue resolve/bulk: load targets through `visible()` and assert `review_owner(...) == actor` or `acl.can("review_queue.resolve", item)`; negative tests for non‑owner and invisible item; move the check into the service, not the router.
A4 Role assignments: add `workspace_id` to `role_assignments` (migration + backfill from scope) and scope list/delete by it; cross‑workspace negative test.
A5 LDAP StartTLS: raise when `start_tls()` returns False; test with a mocked connection.
A6 PPTX export: empty chart/explorer data renders a "No data in scope." text box; test with an empty snapshot and with a restricted viewer.
A7 Layout endpoints and CSV: counts via COUNT … GROUP BY (or `bulk_items()`), cards paged; CSV streams every row; test with 250+ items that WIP breach and row count are correct.

PACKAGE B — access‑control completeness and robustness (second PR)
Review findings 8–23. Required outcomes: share‑token decks drop wellbeing rows and redact people/now for anonymous viewers (B1); insight/project_detail slide data resolved through `visible()` (B2); notification recipients filtered by workspace (B3); `/metric-snapshots` behind `habit.view_team`/`visible()` and wellbeing keys stripped for non‑line‑managers (B4); capture by `item.key` through `visible()` (B5); gateway budget keyed by a workspace derived from the core token or per‑workspace gateway tokens (B6); CEL evaluation under a wall‑clock budget and `matches()` restricted or rejected for nested quantifiers (B7); IdP sync stamps `source="idp:<provider>"` and removes only its own provider's rows (B8); fail fast at startup when env != dev and the session secret is the default or shorter than 32 bytes (B9); product reads, `/classifications?target_type=project|product`, and roll‑ups apply visibility (B10); deck `?src=` honoured only when `__DECK_SRC__` is unset and same‑origin, `image.url` limited to data:/same‑origin (B11); PDF/PNG export via a Procrastinate job with a download URL, or at minimum a bounded semaphore returning 503 (B12); `allowed_roles` enforced in `place()` and the four missing propagation triggers implemented with tests (B13); separate `agent_timeout` (~20 s) in tt_client/tt_cli (B14); migration adding the FK/lookup indexes listed in the P0–2b report M4 (B15); team habit trend computed from team‑aggregated previous window (B16); login throttle keyed by IP+username with backoff, stored in the DB (B17). Also the Low items that are cheap: 400 instead of 500 on bad cursor/size/enum input; `include_inbox` honoured when nested; `MissingEventError` after token bookkeeping; concurrent duplicate `client_id` → `deduplicated`.

PACKAGE C — ADRs for recorded deviations (same PR as B)
Write one ADR each, in docs/decisions/, for the list in §4 of the review (synchronous propagation; router‑level permission checks plus the new rule "any service entry reachable from more than one route checks itself"; workspace manager sees members‑tier items; Inbox invisible to admins; SHA‑256 token hashing; capture hints as source=client; state_id moved by capture; LiteLLM Router not used; in‑memory agent runs; filter compiler location, CTE for `under`, EXISTS for `touched`; static CEL checker and limits; snapshot range as days+as_of; presentation events on SAVED_VIEW; client‑side nested AD groups). Kerberos SSO, Postgres verify‑full and certificate reload stay deferred only if the product owner confirms; otherwise add them to package I.

PACKAGE D — top‑down plan API (plans/02-phase-1-core-domain-and-capture.md §1.2 "Top‑down project plan")
`POST/GET /projects/{key}/plan` creating phases → deliverables → milestones → finish_to_start links in one transaction with `plan.created` events; derived statuses for deliverables, milestones and projects exactly as specified; baseline capture as plan approval and variance in the read; `Milestone.kind` in the API. Tests: a project planned with zero capture rows returns a full tree with `not_started` statuses and null roll‑ups; statuses flip as dates and tasks change.

PACKAGE E — plan ↔ execution linkage and the plan‑only seed (plans/04-phase-3-management-overlay.md §3.11 and scope items 13–14)
Auto‑parenting of captured tasks under deliverables (LLM hint → glossary → trigram ≥ 0.5 → `ReviewItem{kind=unlinked_task}` with top‑3 suggestions, refined by the gateway); `progress_mode=children` default for phase/deliverable types; plan‑tree roll‑ups; dependency re‑evaluation marking `at_risk`; `plan_slip` review kind. Seed: add project `ERP` (3 phases, 9 deliverables, 4 milestones, 2 dependencies, a baseline, zero tasks) and make every list/board/pipeline/snapshot endpoint pass against it.

PACKAGE F — plan slide kinds and the plan‑only rule (plans/08-phase-7-snapshot-presentation-insights.md scope item 2, §7.1 extended snapshot fields, §7.2)
Snapshot gains `phases[]`, `deliverables[]`, milestone `kind/status/completed_at/deliverables_done/total`, project `owner/planned_start/planned_end/status/health/baseline_name`. New slide kinds roadmap, milestones, deliverables, plan_vs_actual, phase_status, project_overview with renderers, config schemas, PPTX mappings. Every slide kind declares `requires` and `render_empty`; activity kinds render "No work reported yet for this scope". Templates "Project plan review" and "Programme roadmap". Acceptance: the ERP deck renders every slide, exports PDF/PPTX, and after two tasks are logged shows progress without editing the presentation.

PACKAGE G — templates as data (plans/08… §7.5)
`presentation_templates` table and `PresentationTemplate` JSON schema (docs/api/presentation-template-schema.json); builtin templates moved from Python constants to apps/presentation/templates/*.json and upserted idempotently at startup; CRUD + :validate/:preview/:export/:import/:duplicate/:share; `POST /presentations/{id}:save-as-template`; `GET /presentation/slide-kinds` publishing config schemas; copy‑on‑instantiate semantics; builder "Templates" tab with form editor, JSON editor with inline errors, import/export. Acceptance criteria are listed in the plan file; all must have tests.

PACKAGE H — external‑LLM generation pack and import (plans/08… §7.6; plans/14-phase-2b-llm-gateway.md author tasks)
`POST /presentations:generation-pack` producing data/, context/, prompt/, template/, manifest.json with redaction levels and stamping; `POST /presentations:import` with schema validation, key resolution against the pack, markdown sanitising, provenance, `--lenient`; gateway tasks `/v1/presentation/author` and `/v1/presentation/template-author` with prompts under prompts/presentation/*/v1 and golden sets; builder "Generate with AI" = pack → gateway → import → preview. The echo round‑trip contract test (pack of an existing deck + echo model → identical deck) is mandatory. Workspace switch to disable external packs.

PACKAGE I — remaining plan scope that is missing
Roadmap editor and "Present this plan" (plans/06-phase-5-web-shell-and-manager-console.md, Setup → Project plan); "Add to presentation" on P9 governance screens; P1 scope never built: RecurrenceRule job and endpoints, Attachment StorageAdapter (local disk), progress_pct recompute job, Comment/Checklist/WorkItemLink/CustomFieldDefinition CRUD; P9 thin editors (scope picker, candidate‑filter builder, notify/auto‑action editors, version history, habit sparklines); accessibility audit (axe in Playwright, ≥ 95) and Storybook/Ladle stories; Schemathesis contract tests for core and gateway; real‑model golden run recorded in docs/llm/gateway.md; Kerberos/Postgres TLS/cert reload if not waived in package C.

Done means, per package: TESTPLAN items all green, `make check` green, DB suite green with timeouts, acceptance criteria of the cited plan sections checked with linked tests, ADRs written, docs and appendix E updated, PR opened against the implementation branch with the review finding numbers or plan sections it closes in the description.

Final report: per package — what changed (by module), tests added and results (counts, coverage per package against appendix B floors), findings closed by number, ADRs written, deviations from the plan and why, and open product questions (at minimum: Kerberos/Postgres TLS deferral, external packs default on/off, share‑token redaction level).
```

## D.6 Prompt for Phases 13–15 (remediation, client role modes, personal tracker)
```
You are continuing the Team Tracker implementation in repository mdaoor/team_tracker.

Branches: plans and reviews are on `main`. The implementation is on `claude/listed-plan-implementation-hygguz` (HEAD 472650d or later). Create one branch per package below from the implementation branch and merge each by PR into it. Never commit to `main`; never rewrite history on a branch you did not create.

Read first, in order: plans/README.md (seven non-negotiable rules; requirements table), plans/00-architecture-and-stack.md (incl. §5.1 client architecture), docs/reviews/2026-10-25-technical-and-ux-review.md (Parts 1 and 2 with file:line and screenshot evidence), plans/appendix-a/b/c, then the phase file for the package you are on. Plans are normative; appendix C settles silent points; unresolved → ADR and continue; never stop on a product question — list it in the final report.

Work method: write TESTPLAN.md per package (one failing test per acceptance criterion or review finding), implement to green, `make check`, DB suite with TT_TEST_DATABASE_URL and pytest-timeout, small conventional commits, update docs/api/openapi.json, docs/api/event-catalogue.md and plans/appendix-e-traceability-matrix.md as you go. A review finding is closed only by a regression test.

PHASE 13 — plans/15-phase-13-remediation-technical-and-ux.md (do in order; packages R1, R2, R3 first)
R1 blocking UX bugs (downloads, builder iframe CSP, delete confirm+undo, toast dedupe+undo, nav gating, share-link reason, selector/seed fixes).
R2 names instead of UUIDs, CLI cues, review-queue rows (reporter/time, Enter), accessibility fixes from the axe table, deck KPI drill/drawer/table toggles.
R3 technical hardening (FK index migration + test, hermetic tests + concurrency fixture + timeouts, job retries + failed-job metric, proxy-aware rate limit, ws_today helper + ruff ban, Docker hardening, CSP nonce, gateway budget reserve/Decimal, secret key, shared engine, worker concurrency, OTel instrumentors, slack tests).
R4 architecture refactors (domain errors, service extraction + router split, builtins seeding + commit guard, DB CHECK/ON DELETE, dedupe/docstrings).
R5 frontend architecture (typed wrappers, lazy routes + ErrorBoundary, responsive shell).
R6 UX feature gaps (review routing + deterministic suggestions + seed fix, capture intent for verb-less lines, setup editors to UX §3.3, Gantt renderer, deck auto-fit, next-week preset, CLI first-run + end-of-day).
Done: all phase-level acceptance criteria in the file, including axe 0 serious, 390 px no horizontal scroll, DB suite < 12 min, chunk < 250 kB gzip, and the re-run UX walkthrough passing.

PHASE 14 — plans/16-phase-14-client-roles-and-management-commands.md
Build in this order: command registry + catalogue endpoint + ClientBatch 0.2 (commands[]) + batch processing with per-command status (207); the ops in the catalogue table, each delegating to existing services; item.merge service with property tests; role presets scrum_master/coach/project_manager and GET /me role_modes; gateway /v1/translate/command with the 80-line golden set; tt_client command outbox, preview, confirmation, schema-generated forms, roles.yaml; CLI `tt role|do|cmd|preview|queue|team now|approve|waive`; desktop/mobile/Slack role switch, command card with effect preview, My queue and Team now panels. Rules: commands validate and may be rejected; capture never blocks; destructive ops need confirmation (`--force` logged); previews never write. Done: every acceptance criterion in the file, ADR-0026/27/28, docs/client/management-commands.md, UX addendum for roles.

PHASE 15 — plans/17-phase-15-personal-time-tracker-and-calendar.md
Build in the numbered task order: encrypted local store + model + RRULE materialisation (Hypothesis across DST); quick-add parser extension + golden; timer; Today/Week/Month + ready-to-send tray + end-of-day and weekly review; Google Calendar adapter (PKCE, syncToken) and Microsoft Graph adapter (MSAL, delta, Teams meeting fields) behind CalendarProvider with recorded fixtures; rules/suggestions with series memory; send path with source.origin and calendar_ref + server dedup + time.unlog + development project + ContextPack additions; privacy settings, first-run, audit, erase; CLI commands. Rules: local-first and private by default; the server never sees calendar contents; tokens only in the OS keyring; one-tap send from wherever an item is shown; personal category has no send path. Done: every acceptance criterion in the file incl. the network-deny test, dedup of a re-sent meeting, development item ACL, ≤ 2 taps from meeting end to sent, mobile 390 px, axe 0 serious; ADR-0029/30/31; docs/client/personal-tracker.md and calendar-integrations.md; UX addendum for the personal tracker.

Final report per phase: what changed by module, tests and coverage per package against appendix B floors, review findings closed by number (Phase 13), deviations from the plans and why, ADRs written, open product questions (at minimum: Kerberos/Postgres TLS waiver status, default share policy for meetings, whether coaches may see development hours, Graph admin-consent for attendance reports).
```
