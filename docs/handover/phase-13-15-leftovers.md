# Hand-over: what is left after the Phases 13–15 leftovers pass

State (2026-10-28): the Phase 13–15 work, the PR #11 review must-fix items (PR #13) and the leftovers pass are on branch **`chore/leftovers`** (PR into `claude/listed-plan-implementation-hygguz`). The previous version of this file listed ~40 items; the ones below are what is genuinely still open.

## How to read the repo
`plans/README.md` (index and the seven non-negotiable rules), `plans/15-…16-…17-*.md` (Phases 13–15), `plans/appendix-c-open-decisions-and-assumptions.md` (decisions and answers adopted for the open product questions), `plans/appendix-e-traceability-matrix.md`, `docs/decisions/ADR-0041…0059`, `docs/reviews/2026-10-25-technical-and-ux-review.md` (the Resolution table says what was verified by which spec), `docs/uat/README.md` + `uat-script.md` + `uat-personal-tracker.md`, `docs/client/{management-commands,personal-tracker,desktop,calendar-integrations}.md`, `docs/web/README.md`, `docs/presentation/README.md`.

## Done in this pass (for orientation)
Atomic multi-step commands with a single commit and the batch `correlation_id`; per-op token scopes; command rate limit and 50-command cap; rejected commands audited (`command_log` + `command.rejected`); `ops/` package; Jira projection of `item.merged`; real role × op matrix, per-op event snapshot and console-parity tests; non-tautological gateway golden; `filelock` 4.2.4 via a uv override (pip-audit clean); modules > 400 lines split; docstrings 100 % in `domain/` + `governance/`; sign-in bucket per (address, username); `uv` image pinned by digest; vacuous tests made real; `client_version` default `0.2`; Roadmap config validation; template delete confirm + undo; web Playwright CI job; table toggle on radar/pipeline; stage popover; product → project → epic tree with drag and Alt+Arrows (same-project moves only); deck `gantt` slide on the shared module; UX re-walk (`apps/web/e2e/uat-walk.spec.ts`); Microsoft delta 410 resync, no resurrection of locally deleted meetings; calendar exclude/suppress/refresh; Mobile Lite PWA (ADR-0057); Tauri tray/notifications/background sync that `cargo check`s (ADR-0058); PyInstaller sidecar build; `tools/smoke_calendar.py`; `external_packs` and `allow_same_level_nesting` workspace settings (ADR-0059).

## Still open, in priority order

### 1. Needs the owner or real accounts (cannot be done from a sandbox)
* **Live calendar smoke**: run `tools/smoke_calendar.py` with real Google and Microsoft test accounts (steps in `docs/uat/uat-personal-tracker.md`, "Live-account smoke"). Only fakes have been exercised; real 404/410/`cancelled` behaviour for deleted events is unverified.
* **Desktop on a real desktop**: tray icon visible, OS notification drawn, global hotkey, close-to-tray, `tauri build`, installers, code signing, updater, macOS/Windows, real icon artwork, OS keyring on three OSs. Only `cargo check`/`clippy`/`cargo test` and a headless start under xvfb were run.
* **Mobile Lite on a real iOS/Android device**; a native app is not planned in v1. Known limits: the plan and API token are stored unencrypted in the browser; same-origin deployment required (the server has no CORS).
* **Remove the `filelock` override** in the root `pyproject.toml` (`[tool.uv] override-dependencies`) when a `litellm` release lifts its `filelock<4` cap.
* The product-question **answers in appendix C are defaults the owner can overrule** (Kerberos proxy-terminated; external packs download-off / in-product-on; share-token redaction `standard`; no admin override of the Inbox rule; no replay-capable event log in v1).

### 2. Code gaps
* **Cross-project item moves** are refused (`400 cross_project_move`, ADR-0059): re-keying would break Jira links, evidence, saved filters, presentations, workflow states, iterations and capture rows. A real move needs a design (keep the old key as an alias).
* **Setup screen toggles** for `external_packs` and `allow_same_level_nesting` (admin API only today).
* **Catalogue `modes` vs the permission model disagree** (scrum masters are offered `risk.*` without `risk.write`; managers are offered iteration/milestone ops without `iteration.write`). Align `roles.yaml`/modes with `ROLES` or the other way round.
* A project-scoped coach / project manager does not get the extra visibility that workspace-scope assignments get (`habit_visible`).
* `tt send` and other hint sites still print `batch_id[:8]` (only `tt sync` prints a unique prefix).
* The desktop shell makes one sidecar call per minute (3.6 s cold start per call); a long-lived `--serve` child is the next step. A stale frozen sidecar answers `unknown verb`: rebuild after adding a verb.
* Routers that still carry some select-and-add logic: `query_layouts`, `item_fields`, `governance_habits`, `presentation_templates`.
* The `gantt` slide `baseline` draws only when items carry `baseline_end`, which the snapshot does not send yet. The share link cannot be re-shown after a reload (the server returns the token only on creation). A project briefly shows under both products right after a move (two refetches).

### 3. Verification not done
* UAT: §1 (CLI preview, offline queue), §4.3 and §6.1 (translator / gateway), §11 (recurrence needs the worker), §12 (capture-scoped token), §13 (an actual erase), §14 (certificate reload); UX 7 (review row wrapping at 390 px) and UX 11 (dependency arrows in web Views) are not asserted by a spec.
* The new **`web-e2e` job and `perf` step of the CI workflow** have run locally only; check their first GitHub run (timing budgets on runners, Chromium install).
* Technical-review rows 2–10 and 13–15 in the Resolution table are marked "claimed, not re-verified in the web pass" (server rows are covered by their own tests).

### 4. Test hygiene
Timing/e2e tests flake under CPU contention (`test_tls`, `e2e/test_cli_flow`, `e2e/test_concurrency`, perf): rerun alone before calling them real. The local Postgres in the dev container sometimes stops (`pg_ctlcluster 16 main start`). Use a private `TT_TEST_DATABASE_URL` database when several agents run suites in parallel.

## Ground rules to keep
Work autonomously on a branch off the integration branch; never commit to `main`; no history rewrites on shared branches; tests ship with code (failing test first); keep `docs/api/openapi.json` (`uv run python -m tt_server.openapi docs/api/openapi.json`), `apps/web/src/api/schema.d.ts` (`pnpm --filter @tt/web gen:api`), `docs/api/event-catalogue.md`, `plans/appendix-e-traceability-matrix.md` and ADRs current; commit trailers `Co-Authored-By` and `Claude-Session`; open PRs only against `claude/listed-plan-implementation-hygguz`; never ignore a vulnerability or skip a test to get green.

Local gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy packages/schema/src packages/client_core/src apps/cli/tt_cli apps/server/tt_server apps/llm_gateway/tt_llm packages/filter_grammar/src packages/cel_policies/src`; `TT_TEST_DATABASE_URL=postgresql+psycopg://tt:tt@localhost/tt_test uv run pytest apps/server/tests -q -n 4 --timeout 120 -m "not perf"`; `uv run pytest packages apps/cli apps/llm_gateway apps/slack_bot -q`; `pnpm --filter @tt/web lint|typecheck|test|build`, `pnpm --filter @tt/desktop lint|typecheck|test`, `pnpm --filter @tt/presentation lint|test|build`; Playwright needs `TT_CHROMIUM`, a seeded e2e database and `make presentation` (see `docs/web/README.md`).

---

## Paste-ready prompt for the next LLM

> You are continuing the Team Tracker implementation (repo `mdaoor/team_tracker`). Check out the integration branch `claude/listed-plan-implementation-hygguz` (or `chore/leftovers` if its PR is still open). Read `docs/handover/phase-13-15-leftovers.md` first, then `plans/README.md`, the Phase 13–15 plans and ADRs 0041–0059. Work autonomously and never stop to ask product questions: choose a sensible default, record it in appendix C or an ADR, and list it in your final report. Do sections 2 (code gaps) and 3 (verification) of the hand-over document; section 1 needs the owner or real accounts, so only prepare and report on it. Create a branch off the integration branch, make small commits (trailers `Co-Authored-By: <your model>` and the session URL), push, and open a PR into `claude/listed-plan-implementation-hygguz`; never commit to `main`, never rewrite shared history, never skip, disable or quarantine a test or ignore a vulnerability to get green. Every change ships with a failing-first test; keep the OpenAPI snapshot, web schema types, event catalogue, appendix E and ADRs in sync. Run the local gate from the document before each push and watch the PR's CI. Finish with an honest report per item: what changed, tests and counts, what you could not do and why, and any new open questions.
