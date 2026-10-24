# Resolution of the 2026-10-24 implementation review

The review itself lives on `main` (`docs/reviews/2026-10-24-implementation-review.md`). This file records what was done about it. Test mapping per finding:
`apps/server/tests/review/TESTPLAN.md`.

## Packages and pull requests (stacked, each against the previous branch)
| Package | Branch / PR | Content |
|---|---|---|
| A security (findings 1-7) | `fix/review-2026-10-24` / #4 | deck XSS, token ceiling, review ownership, role-assignment tenancy, StartTLS, PPTX empty, paging |
| B completeness (8-23 + cheap Low) | same | ACL on shared decks / insights / metrics / products / capture, notification tenancy, CEL cap, IdP sync, secrets, indexes, render bound, ... |
| C ADRs | same | ADR-0016 ... ADR-0035 |
| D top-down plan API, E linkage + ERP seed | `feat/d-e-plan-api-and-linkage` / #5 | plan API, derived statuses, baselines, roll-ups, unlinked-task review rows, plan-only project |
| F plan slide kinds | `feat/f-plan-slides` / #6 | six plan kinds, plan-only rule, extended snapshot |
| G templates as data | `feat/g-templates-as-data` / #7 | table, schema, CRUD/validate/preview/import/export, builder tab (ADR-0037) |
| H generation pack + import + author | `feat/h-generation-pack` / #8 | pack, strict/lenient import, gateway tasks, Generate with AI |
| I remaining scope + phases 11/12 | `feat/i-remaining-plan` / #9 | see CHANGELOG |

## Findings 1-23
All 23 are fixed with at least one regression test (table in the TESTPLAN). Three were closed with caveats:
* **13** budget keyed by workspace: closed by workspace-bound service tokens (`TT_LLM_WORKSPACE_TOKENS`); an operator who does not set them keeps the old trust-the-caller behaviour.
* **19** exports: Chromium renders are bounded (2 at a time, 503 `render_busy` otherwise); they are still synchronous in the request (ADR-0034), not a job.
* **22** indexes: added and checked against the migrated database; no query-plan regression test.

## Low items not done
`include_inbox` ignored when nested; builder hint badge for activity kinds with zero items; `test_snapshot_perf` bound relaxed to 8 s (real-size cold snapshot measured separately, `docs/ops/load-report.md`).

## Plan features the implementation predated (review section 3)
All built (D-I), except: Kerberos is proxy-terminated only; real-model golden run wired but **not run**; roadmap editor edits dates/approves but does not add phases or deliverables (create/extend via API).

## Contract fuzzing results
Schemathesis against the seeded app found and fixed 500s on negative/huge `limit`, NUL bytes, invalid enum filters, dangling foreign keys and an overflowing `upcoming` filter. Response-schema conformance is not asserted (most responses are untyped objects in OpenAPI).

## Unresolved / open
See `docs/uat/known-limitations.md`, ADR-0036 (Kerberos), ADR-0040 (no replay rebuild). GitHub CI on these branches was already failing before this work (python job, from the P2b commit onward); the cause could not be read from the logs available here and is **not diagnosed**.

## Final verification (branch `feat/i-remaining-plan`)
* Python: 2775 passed, 2 failed in a coverage run (23 min); the two failures are timing budgets (`test_query_perf`, `test_governance_perf`) that pass when run alone (3 passed in 5.5 min). Total line coverage of the measured packages: **90 %** (19,520 statements).
* ruff, ruff format, mypy (222 files): clean. Web vitest 49, desktop vitest 13, presentation vitest 15. Playwright: web 40 passed (includes axe = 100 on 14 screens, walkthrough, CSP), presentation 12 passed.
* Not run: real-model goldens, Tauri/mobile builds, Helm lint/kind, a real Slack workspace, a real Jira / AD / OIDC provider, walkthrough video capture (ffmpeg build mismatch in the sandbox).
