# Appendix B — Testing strategy and Definition of Done

## Test pyramid
| level | tool | what | where |
|---|---|---|---|
| Unit | pytest, Hypothesis | schema parity, parsers, filter compiler, CEL functions, propagation rules, ACL matrix | `packages/*/tests`, `apps/server/tests/unit` |
| Service/integration | pytest + testcontainers Postgres | domain services through `UnitOfWork`, migrations round‑trip, jobs, event catalogue completeness | `apps/server/tests/integration` |
| API/contract | httpx ASGI client, schemathesis against OpenAPI | every route, auth, ACL, idempotency, error shapes | `apps/server/tests/api` |
| Oracle | pytest | seed → snapshot/violations/habits equal `tools/gen_snapshot.py` | `apps/server/tests/oracle` |
| Web unit | Vitest + Testing Library | components, hooks | `apps/web/src/**/*.test.tsx` |
| E2E | Playwright | UX flows §7.1–7.3, keyboard, print, no external calls | `apps/web/e2e`, `apps/presentation/e2e`, `apps/desktop/e2e` |
| Load | k6 (or Locust) | budgets in P12 | `ops/load` |
| Golden (LLM) | custom harness | translator accuracy on the golden set | `packages/client_core/tests/golden` |

## Fixtures
- `seed_workspace` (function‑scoped transaction rollback) built from `tools/seed/demo.py` with `as_of = 2026‑10‑23T18:30+03:00` frozen via `freezegun`/`time-machine`.
- Factories for every model (`factory_boy`), Hypothesis strategies generated from the schema package.
- `llm_stub` fixture: deterministic translator returning canned `ClientBatch`es; real‑model tests are opt‑in (`-m llm`).

## Commands that must pass before a phase closes
```
make lint        # ruff check + ruff format --check + eslint + prettier --check
make typecheck   # mypy --strict (schema, filter_grammar, cel_policies, server/domain) + tsc --noEmit
make test        # pytest -q (unit+integration+api+oracle)  with coverage gates
make e2e         # playwright (requires make dev or CI services)
make openapi     # regenerates docs/api/openapi.json and web client; CI fails on diff
make check       # all of the above except e2e
```
Coverage floors: `domain/capture` 85 %, `packages/*` 90 %, server overall 75 %, web 70 % statements.

## Definition of Done (per phase)
- [ ] All acceptance criteria in the phase file are checked with a linked test or recorded manual check.
- [ ] Exit checklist complete; ADRs written; docs updated; event catalogue updated.
- [ ] `make check` and `make e2e` green in CI on the phase branch; merged to `main` by PR with the phase file linked.
- [ ] Seed and oracle tests still pass (regressions in earlier phases block).
- [ ] No `TODO(Pn)` markers for phases ≤ the current one remain.
- [ ] Traceability matrix (appendix E) updated.
- [ ] Handover notes section written for the next phase.
