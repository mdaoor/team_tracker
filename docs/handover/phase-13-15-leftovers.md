# Hand-over: what is left after Phases 13–15

State at hand-over (2026-10-27): everything is on **`feat/phase-14-server`**, PR **mdaoor/team_tracker#11** into `claude/listed-plan-implementation-hygguz`. On head `91d85c3` CI is green for `python`, `web`, `presentation` and `helm`; **`security` is red** (see item 1). The other `feat/phase-13-*`, `feat/phase-14-client` and `feat/phase-15-personal-tracker` branches are already merged into it and can be deleted once the PR merges.

## How to read the repo
`plans/README.md` (index), `plans/15-…16-…17-*.md` (Phases 13–15), `plans/appendix-c-open-decisions-and-assumptions.md`, `plans/appendix-e-traceability-matrix.md`, `docs/decisions/ADR-0041…0056`, `docs/reviews/2026-10-25-technical-and-ux-review.md` (has a "Resolution" section), `docs/uat/README.md` + `uat-script.md` (§15 commands, §16 personal tracker), `docs/client/management-commands.md`, `docs/web/README.md`.

## Leftovers, in priority order

### 1. Decide: `security` job (needs the owner)
`pip-audit` reports 3 CVEs in `filelock 3.32.7` (CVE-2026-112940, -112913, -112907; fixed in 4.2.1). It is pulled in by `litellm`, which pins `filelock<4`; litellm 1.106.4 still does. Options: (a) add `--ignore-vuln` for the three IDs in `.github/workflows/ci.yml` with a note, until litellm lifts the cap; (b) replace litellm in `apps/llm_gateway`. **Do not weaken the gate without the owner's explicit yes** (an assistant was blocked from doing so).

### 2. Phase 14 gaps (code)
* **Per-token rate limit for commands** (plan §8): not implemented. `main.py` rate-limits only `POST …/capture/batches`; add `/commands` and the `commands[]` part of batches (reuse `SlidingWindow`, key by token), with a test.
* **Jira projection of `item.merged`** (ADR-0050 "open"): nothing in `integrations/jira` reacts. When the project has Jira enabled, comment on the source issues and link them to the target.
* **Acceptance "snapshot test of event types per op"**: `tests/api/test_commands.py` asserts the `payload.command` tagging but not the exact event types each op emits compared with the web console. Add a table-driven test (op → expected event types).
* **Rejected commands are not logged** (only applied ones, ADR-0056). Decide whether an audit row for rejections is wanted.
* **`api/commands/ops.py` is 1179 lines**: split by group (`ops_projects.py`, `ops_items.py`, `ops_teams.py`, `ops_governance.py`, …), keep `REGISTRY` registration via the package `__init__`.
* Client/server contract guesses to confirm with a manual run: `review.resolve` `choice` object semantics, `context_note.add` scope mapping, `x-picker`/`format` hints (the server schemas carry none; the client uses name hints).

### 3. Phase 13 residue
* Modules still over ~400 lines: `exports/pptx_builder.py` (697), `query/service.py` (531), `domain/plan/service.py` (500), `query/compile.py` (472), `governance/engine.py` (453), `governance/policies.py` (452), `domain/snapshot/assembler.py` (450), `domain/overlay/propagation.py` (430), `db/models/projects.py` (418), `domain/presentation_templates.py` (404).
* Routers still carrying orchestration: `auth.py`, `llm.py`, `capture.py` and others not yet thinned (the six in the plan were done).
* About 32 public items in `domain/`/`governance/` lack a docstring (mostly Pydantic schema models, where a class docstring would change the OpenAPI output; use a comment or `Field(description=…)` and regenerate `docs/api/openapi.json`).
* R6.3: the stage editor is an inline dialog, not a popover anchored to the chip; the product → project → **epic** tree was not checked beyond product → project.
* R6.4: the deck's `gantt` slide kind (`apps/presentation/src/renderers/time.js`) still has its own drawing code; the Roadmap server config keys `rows`/`zoom` are not validated by the server.
* Verify (not just assert) these UX items from the review: 3, 5, 6, 8, 9, 12, 13 (the "Resolution" table says fixed; only the ones with an e2e test were exercised). Walk `docs/uat/uat-script.md` end to end against a seeded stack and correct the table.

### 4. Phase 15 / clients
> **Update 2026-10-28 (`leftovers/p15`)**: Tauri shell now compiles (`cargo check`/`clippy`/`test` on Linux) with tray, notifications, background sync; the PyInstaller sidecar builds and answers; a PWA "Mobile Lite" exists; calendar 410/resurrection/exclude/suppress/refresh are done; `tools/smoke_calendar.py` exists but was **not run live**. See `docs/client/desktop.md`, ADR-0057/0058, `docs/uat/uat-personal-tracker.md`. Still open: real-desktop verification, installers/signing/updater, macOS/Windows, native mobile, the live smoke run, keyring on three OSs.
* Nothing has been compiled for **Tauri** (desktop palette) or the mobile shell; `apps/desktop/sidecar/build.sh` (PyInstaller) was never run. Notifications through the OS are open.
* Google Calendar / Microsoft Graph adapters are tested against fakes only; a real-account smoke test (OAuth consent, token refresh, Teams meetings) is outstanding. See `docs/uat/uat-personal-tracker.md` and `docs/uat/known-limitations.md`.
* `ClientBatch.client_version` still defaults to `"0.1"` (the parity test pins the spec file); `CLIENT_SCHEMA_VERSION = "0.2"` is what new clients send. Decide whether to bump the spec file.

### 5. Test and CI hygiene
* Timing tests are marked `@pytest.mark.perf` and run in their own CI step without coverage; they are contention-sensitive locally under `-n 6` (also `test_tls`, `e2e/test_cli_flow`, `e2e/test_concurrency`: rerun alone before calling them real failures).
* The local Postgres in the dev container stopped twice mid-run (`pg_ctlcluster 16 main start`); the full local server suite was not re-run to completion on `91d85c3`. CI's `python` job (serial, with coverage 90 %) is the reference.
* `ruff format` touched ~25 unrelated files once; `prettier --check` was cleaned for web/presentation; keep both clean.
* Refresh the **PR description** (it still says R5.2/R5.3 not started, sidecar missing, docstrings below target: all now done).

### 6. Open product questions (record the answer in `plans/appendix-c`)
Kerberos / Postgres TLS deferral; external generation packs on by default?; share-token redaction level; Inbox admin override.

## Ground rules to keep
Work autonomously on a branch off `feat/phase-14-server`; never commit to `main`; no history rewrites on shared branches; tests ship with code; keep `docs/api/openapi.json` (`uv run python -m tt_server.openapi docs/api/openapi.json`), `docs/api/event-catalogue.md`, `plans/appendix-e-traceability-matrix.md` and ADRs current; commit trailers `Co-Authored-By` and `Claude-Session`; open PRs only against `claude/listed-plan-implementation-hygguz`.

Local gate (run before every push): `uv run ruff check . && uv run ruff format --check . && uv run mypy packages/schema/src packages/client_core/src apps/cli/tt_cli apps/server/tt_server apps/llm_gateway/tt_llm packages/filter_grammar/src packages/cel_policies/src`; `TT_TEST_DATABASE_URL=postgresql+psycopg://tt:tt@localhost/tt_test uv run pytest apps/server/tests -q -n 6 -m "not perf"`; `pnpm --filter @tt/web lint|typecheck|test|build`, `pnpm --filter @tt/desktop lint|typecheck|test`, `pnpm --filter @tt/presentation lint|test|build`; Playwright needs `TT_CHROMIUM`, a seeded `tt_e2e` database and `make presentation` (see `docs/web/README.md`).

---

## Paste-ready prompt for the next LLM

> You are continuing the Team Tracker implementation (repo `mdaoor/team_tracker`). Check out `feat/phase-14-server` (PR #11 into `claude/listed-plan-implementation-hygguz`; CI is green except the `security` job). Read `docs/handover/phase-13-15-leftovers.md` first, then `plans/README.md`, the Phase 13–15 plans (`plans/15-…`, `16-…`, `17-…`), `plans/appendix-c-open-decisions-and-assumptions.md` and the ADRs 0041–0056. Work autonomously and never stop to ask product questions: choose a sensible default, record it in appendix C or an ADR, and list it in your final report. Do the leftovers in the order of that document, except item 1 (the `filelock` CVE decision), which belongs to the owner: do not ignore or suppress vulnerabilities, report it. Create a branch off `feat/phase-14-server`, make small commits (trailers `Co-Authored-By: <your model>` and the session URL), push, and open a PR into `claude/listed-plan-implementation-hygguz`; never commit to `main`, never rewrite shared history, never skip or disable a test to get green. Every change ships with tests; keep the OpenAPI snapshot, event catalogue, appendix E and ADRs in sync. Run the local gate from the hand-over doc before each push and watch the PR's CI. Finish with an honest report per item: what changed, tests and coverage, what you could not do and why, and any new open questions.
