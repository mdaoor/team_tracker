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

## D.7 Must‑fix prompt for PR #11 (phases 13–15), after the 2026‑10‑25 PR review
```
You are fixing the must-fix findings of the review of PR #11 in repository mdaoor/team_tracker.

Branches
- PR #11 is feat/phase-14-server → claude/listed-plan-implementation-hygguz (HEAD 91d85c3 at review time). Create `fix/pr11-must-fix` from feat/phase-14-server, push it, and open a PR into feat/phase-14-server (so PR #11 absorbs it) — or push to feat/phase-14-server directly if you own it. Never commit to `main`; never rewrite history on a branch you did not create.
- The review is on `main`: docs/reviews/2026-10-25-pr11-review.md (merge mdaoor/team_tracker#12 first if still open; until then read it from branch claude/optimistic-pasteur-lb1pvw). Parts 1–4 carry file:line evidence at HEAD 91d85c3; re-locate lines if the files moved. The plans it checks against are plans/15-*, 16-*, 17-*.md; the seven non-negotiable rules are in plans/README.md.

Rules of engagement
- One failing regression test per item BEFORE the fix; the fix is done when that test passes and `make check` is green. A finding without a test that would fail on the old code is not closed.
- Run the DB suite with TT_TEST_DATABASE_URL set, `-n 4 --timeout 120 -k "not perf"` (it takes ~7 min). Run `pnpm --filter @tt/web test`, `pnpm --filter @tt/web build`, `pnpm --filter @tt/presentation test`, and the package suites (`uv run pytest packages apps/cli apps/llm_gateway apps/slack_bot`).
- Small conventional commits, one per item (`fix(cmd): …`, `fix(personal): …`). Update docs/api/openapi.json when a contract changes; add ADR amendments where named below.
- Do not widen scope to the should-fix list; list anything you notice for later in the final report.

ITEM 1 — pending_approvals contract (review Part 2 H2; UX Part 4 §(c) 2)
Server (apps/server/tt_server/domain/contextpack_roles.py:61-76) returns an int; clients expect a list. Change the server to return `pending_approvals: [{id, title, project_key, requested_by, requested_at}]` (keep a separate `pending_approvals_count` if anything needs the number). Verify tt_client/pack.py:80-81,130-132, apps/cli (`tt approve`), Slack `/tt approvals`, desktop types.ts:28 and both gateway tasks consume the list. Tests: (a) server test asserting the list shape; (b) a client parity test that loads a REAL `/context-pack` response from the seeded API into `ContextPack` and calls `.compact()` without error; (c) CLI test: `tt approve` with no id lists approvals numbered and `tt approve 1` decides the first.

ITEM 2 — `tt sync` auto-confirms destructive commands (Part 2 H1)
packages/client_core/src/tt_client/outbox.py:160-164,182 stamps `_meta.confirmed=True` on every queued command. Only stamp `confirmed` for commands whose outbox entry carries `confirmed_at` set by `plan_confirmation` (CLI `tt do`/`tt cmd`/`tt send`, sidecar `_send`, Slack confirm). `tt sync` must skip unconfirmed destructive commands, print "kept in the outbox: needs confirmation (tt send <id> --yes)", and `_deliver` (apps/cli/tt_cli/main.py:67-97) must print per-command results and exit 1 when any command was rejected. Tests: stage a destructive command with `--no-send`, run `tt sync`, assert the server never received it and the entry remains; a confirmed one is delivered; a rejected command makes `tt sync` exit non-zero with the reason shown.

ITEM 3 — `manage` token reaches people-admin ops via /commands (Part 2 H3)
apps/server/tt_server/api/commands/execute.py:51-52 only requires scope `manage`. Add `token_scope` to the command spec (default `manage`; `admin` for team.create|add_members|remove_members, reporting_line.set, project.add_member, dimension.value.add, policy.activate|deactivate, tag.merge) and check it in `run()` before the permission check, returning per-command `rejected` with code `insufficient_scope`. Test: a token minted with scopes [capture, read, manage] can run item.* ops but gets `insufficient_scope` on team.create and reporting_line.set, via both POST /commands and /capture/batches; an admin-scoped token succeeds. Add a registry test asserting every op declares token_scope ∈ {manage, admin}.

ITEM 4 — rate limit and batch cap for commands (Part 2 H4, M9)
Add `rate_commands_per_min` (default 120) to settings and include POST /commands and POST /items:merge in the credential-keyed window (apps/server/tt_server/main.py:172-180, ratelimit.py). Add `max_length=50` to `ClientBatch.commands` in packages/schema (tt_schema.client:121) and return 422 with a pointer when exceeded. Tests: 121st command in a minute → 429 with Retry-After; a batch with 51 commands → 422 naming /commands.

ITEM 5 — personal quick-add lines leave the device without opt-in (Part 3 High 1)
apps/cli/tt_cli/personal.py:212-220 and tt_client/personal/service.py:add call the gateway for every line; parser/personal_llm.py:fetch sends the line, actor, project keys AND the server bearer token. Add personal setting `privacy.assist` (default `off`), shown on the first-run sheet and in `tt privacy`; call the gateway only when `on` AND the deterministic parser did not already decide (`kind_source == "default"`); never send the server token (the gateway has its own caller auth). Tests: with `[llm] gateway_url` configured and assist off, `tt add "therapy 5pm"` makes no HTTP request (use the existing network-deny fixture); with assist on, the request body contains no `Authorization` header and no token field. Amend ADR-0053 with the decision.

ITEM 6 — auto-sent meetings carry title and attendee handles (Part 3 High 2)
tt_client/personal/send.py:44-50,98,103. When `share.meeting=auto` or `send_each_instance` is set, send a neutral title ("Meeting · 1 h") and omit attendee handles unless the person has confirmed the note template once for that series (store `note_consent_at` on the series memory; the tray shows the exact note before the first auto-send of a series). Manual sends from the tray keep showing the full note for the person to edit. Fix ADR-0054 §4 and the send.py docstring to state what is actually sent in each mode. Tests: auto mode without consent → title "Meeting · 1 h", no handles, no raw calendar subject in `raw_input`; after consent → the template is used; manual send → whatever the person saw in the tray.

ITEM 7 — CLI/desktop payload mismatches and end-of-day (Part 4 §(c) 1–3, §(d) 1; Part 4 issue 15)
7a `tt queue`: CLI reads id/summary/title/default_choice but the API returns ref/item_key/item_title/suggested.choice; the desktop QueuePanel reads the same wrong fields. Map once in tt_client (one `ReviewRow` dataclass from the API shape) and render `INBOX-sara-1 · <title> · @sara · 3 min → WEB [Enter]`. Test against a recorded real `/review-queue` response.
7b `tt team now`: payload key mismatch (person/item). Map in tt_client; show `@sara · WEB-114 fix the login redirect loop · 12 min`. Test with a recorded `/activities?team=mine` response.
7c Daily log: a DailyLog draft must exist as soon as time is logged or an activity ends for that person-day (server: create/update the draft in the ingest path for time.logged and activity.ended, in the person's zone), and `GET /daily-logs?date=` must return it. `tt today` then shows it; `tt eod --confirm` confirms it and prints a sentence, never raw JSON. Tests: server test "log 45 min → draft exists for that date with minutes_total 45"; CLI test for `tt today` and `tt eod --confirm` output.

ITEM 8 — glossary learning regressed on review resolve (Part 4 §(a) 9)
Resolving an unclassified_project row to a project no longer writes a learned glossary entry (previous build did) and the "Don't learn" affordance is gone. Restore learning in domain/overlay/review.py resolve (phrase = the item's raw title words that matched, → project), keep the Undo and add "Don't learn" to the toast (removes the learned row). Tests: server test asserting a `glossary_entries{source=learned}` row after resolve and its removal via the don't-learn endpoint; the existing e2e for the toast.

ITEM 9 — commit allow-list unpinned (Part 1 High 1)
`uow.commit_bookkeeping()` (db/uow.py:61-67) commits anything pending and resets the projection flag; it is now called mid-workflow (integrations/jira/sync.py:196,284,336,350; api/deps.py:142) and `commit(allow_unlogged=True)` is used in api/commands/execute.py:160 and recurrence.py:151. Make `commit_bookkeeping()` raise `MissingEventError` if `_touched_projection` is already True (so it can only commit bookkeeping rows), fix any caller that relied on the leak by emitting the proper event, and add tests/unit/test_commit_allowlist.py that asserts the exact permitted call sites of `commit_bookkeeping`, `commit_derived` and `allow_unlogged=True` (a frozen list in the test; adding a site requires editing the test). If the command executor needs `allow_unlogged`, replace it with a real `command.applied` event so the guard holds.

ITEM 10 — CI security job (pip-audit)
`filelock 3.32.7` has three CVEs; bump to ≥ 4.2.1 in uv.lock (`uv lock --upgrade-package filelock`), re-run `uv run --with pip-audit pip-audit …` locally as in .github/workflows/ci.yml, and confirm the job is green on the PR.

Also update the PR #11 description: R5.2/R5.3 are done; router max is 391 lines; docstring coverage 94 %; the missing desktop piece is the PyInstaller sidecar binary and Tauri build (Phase 14), not the entry point; add the ten items above as "fixed in <commit>".

Final report: per item — test added (path::name), fix (files), suite results (counts), anything out of scope you noticed. Then run the review doc's UX re-walk commands for `tt approve`, `tt queue`, `tt team now`, `tt today`/`tt eod --confirm`, `tt sync` with a staged destructive command, and paste the terminal output.
```
