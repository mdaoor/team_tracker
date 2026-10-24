# Phase 0 — Bootstrap: repo, tooling, schema package, migrations, seed

## Goal
A running, empty, tested skeleton: `make dev` brings up Postgres + API + worker + web; `make test` is green; the schema package exists and is the single source of field names; the first Alembic migration creates every table in object model §3–§10 (even the ones no API uses yet) so later phases only add indexes and constraints.

## Depends on
Nothing.

## Inputs
- `docs/spec/team_tracker_models.py` (port 1:1)
- `plans/00-architecture-and-stack.md` §3 layout, §4.6–4.8 conventions
- `plans/appendix-a-api-conventions.md`, `appendix-b-testing-and-dod.md`

## Scope
1. Monorepo scaffolding (uv + pnpm workspaces), Makefile, docker‑compose, `.env.example`, pre‑commit, CI (GitHub Actions: lint, typecheck, test, build web).
2. `packages/schema` (`tt_schema`): enums and Pydantic v2 models ported from the dataclasses; `layer` metadata on `WorkItem` fields; JSON Schema export for `ClientMessage`/`ClientBatch`; `ALL_MODELS` registry.
3. `apps/server`: FastAPI factory, settings, `/healthz`, `/readyz`, structured logging, OpenTelemetry wiring (no exporter in dev), error handler per appendix A.
4. SQLAlchemy models for **all** tables + Alembic `0001_initial`. Extensions: `pg_trgm`, `ltree`, `btree_gist`, `uuid-ossp` not needed (ids generated in app).
5. `Event` table + `EventWriter` + Procrastinate setup (schema in the same DB) — the write path everyone else uses.
6. `UnitOfWork` abstraction (async session + event buffer + job enqueue on commit).
7. Test harness: testcontainers Postgres, `conftest.py` with `uow`, `client` (httpx ASGI), factory fixtures (`factory_boy`), Hypothesis strategies for every enum/model in the schema package.
8. Seed command `tt-admin seed demo` that creates the `acme` workspace with the people, teams, products, projects, epics, milestones, risks and items of `tools/gen_snapshot.py` (ids deterministic from a fixed seed so tests can reference `WEB-101`).
9. `apps/web` Vite+React+TS skeleton with the design tokens copied from the prototype `:root` block, dark/light, a `/healthz` page. No features.
10. `docs/api/event-catalogue.md` initialised with the list in appendix A.

## Out of scope
Any business endpoint; auth beyond a stub `X-Debug-Actor` header in dev (replaced in P1/P4).

## Deliverables
- `make dev`, `make test`, `make lint`, `make migrate`, `make seed`, `make openapi` (writes `docs/api/openapi.json`).
- CI green on a clean clone.
- `packages/schema/README.md` explaining how to regenerate JSON Schema and the `layer` rules.

## Detailed tasks

### 0.1 Workspace & tooling
- `pyproject.toml` root with `[tool.uv.workspace] members = ["packages/*", "apps/server", "apps/cli", "apps/worker"]`.
- Ruff config: line length 110, `select = ["E","F","I","UP","B","ASYNC","S"]`, `S` ignores for tests.
- mypy strict for `packages/schema`, `packages/filter_grammar`, `packages/cel_policies`, `apps/server/tt_server/domain`.
- `package.json` root with `pnpm-workspace.yaml` → `apps/web`, `apps/presentation`, `apps/desktop`.
- `docker-compose.yml`: services `db` (postgres:16, `POSTGRES_DB=tt`), `server` (uvicorn --reload), `worker` (`procrastinate worker`), `web` (vite), profile `llm` → `ollama/ollama` with a volume and `OLLAMA_MODELS`.
- `Makefile` targets as above; `make check` = lint + typecheck + test.
- GitHub Actions: `ci.yml` with jobs `python` (uv sync, ruff, mypy, pytest with services: postgres) and `web` (pnpm i, lint, typecheck, vitest, build).

### 0.2 Schema package (`tt_schema`)
- One module per section of the models file: `enums.py`, `tenancy.py`, `dimensions.py`, `process.py`, `products.py`, `projects.py`, `items.py`, `capture.py`, `governance.py`, `views.py`, `acl.py`, `client.py`.
- Every dataclass becomes a `pydantic.BaseModel` with `model_config = ConfigDict(from_attributes=True, use_enum_values=False, extra="forbid")`. Keep **exact** field names, types, defaults. `Decimal` stays `Decimal` (serialised as string in JSON — document this in appendix A).
- `Base` fields (`id, created_at, updated_at, created_by, deleted_at`) via a mixin.
- `WorkItem` fields carry `Field(json_schema_extra={"layer": "capture"|"overlay"|"system"|"either"})` per object model §7 table. Export `CAPTURE_FIELDS: frozenset[str]`.
- `client.py`: `ClientTaskRef`, `ClientMessage`, `ClientBatch`, `ClientAction` exactly as in the models file; `client_message_json_schema()` returns a Draft 2020‑12 schema suitable for Ollama's `format` parameter (no `$ref` cycles; enums inline).
- `registry.py`: `ALL_MODELS` tuple in the same order as the source.
- Tests: for each dataclass in the original file (imported from `docs/spec/team_tracker_models.py` as a test fixture) assert that the Pydantic model has the same field names, same required set, same enum values. This is the port's correctness proof.

### 0.3 Database layer
- `tt_server/db/base.py`: declarative base, `TimestampMixin`, `SoftDeleteMixin`, `WorkspaceScopedMixin` (`workspace_id` + index).
- One SQLAlchemy model per table. Naming: snake_case plural table names (`work_items`), PK `id text`, FKs `…_id text` with `ON DELETE RESTRICT` by default (soft delete is the norm), composite uniques:
  - `workspace_members (workspace_id, person_id)`, `(workspace_id, handle)`
  - `projects (workspace_id, key)`; `work_items (project_id, key)`
  - `dimension_values (dimension_id, key)`; `dimensions (workspace_id, key)`
  - `classifications (target_type, target_id, dimension_id, value_id)`; partial unique on `(target_type, target_id, dimension_id) WHERE dimension.cardinality = single` is enforced in service code + a deferred check (document).
  - `stage_placements`: exclusion constraint `EXCLUDE USING gist (target_type WITH =, target_id WITH =, process_id WITH =, tstzrange(entered_at, exited_at, '[)') WITH &&)` → at most one current placement per target per process.
  - `events (client_id)` unique where not null → idempotency.
- Indexes: `work_items USING gin (title gin_trgm_ops)`, `work_items USING gist (path)` (ltree), `events (workspace_id, occurred_at desc)`, `events (entity_type, entity_id)`, `activities (person_id) WHERE status='active'`, `violations (workspace_id, status, severity)`.
- `alembic/` with `env.py` reading the same `Settings`; `0001_initial`. Rule for later phases: **never edit 0001**; add migrations.
- Migration test: run `alembic upgrade head` then `alembic downgrade base` then `upgrade head` on a fresh container.

### 0.4 Event writer & jobs
- `tt_server/events/writer.py`: `EventWriter.append(event: Event)` buffers; `UnitOfWork.commit()` flushes events then calls `outbox.dispatch(events)` which enqueues `procrastinate` jobs declared in `jobs/registry.py` keyed by `event_type` prefix (empty registry for now).
- `tt_server/events/catalogue.py`: `EVENT_TYPES: dict[str, EventSpec]` loaded from `docs/api/event-catalogue.md` front‑matter or a YAML; a test fails if a service emits an unknown type.
- Procrastinate app with `PsycopgConnector` pointed at the same DB; `procrastinate schema --apply` in `make migrate`.

### 0.5 Seed
- `tools/seed/demo.py`: pure functions that transform the `gen_snapshot.py` structures into schema‑package models and persist them through the (minimal) repositories. Because P1–P3 services don't exist yet, the seed in P0 inserts directly via SQLAlchemy and is **replaced in P3** by a version that goes through the ingestion + overlay APIs (so the seed doubles as an end‑to‑end test). Mark with `# TODO(P3): route through services`.

### 0.6 Web skeleton
- Vite React TS, ESLint, Prettier, Vitest, Playwright config; `src/theme/tokens.css` from the prototype; `src/api/client.ts` generated from `docs/api/openapi.json` via `openapi-typescript`; `make openapi` regenerates both.

## Data & API contracts
- `GET /healthz` → `{"status":"ok"}`; `GET /readyz` checks DB + procrastinate schema.
- `GET /api/v1/meta/schema/client-message` → the JSON Schema used by translators (served so clients can fetch the exact version).

## Acceptance criteria
- [ ] `git clone && make dev` on a machine with Docker gives a healthy stack in < 5 min with no manual steps.
- [ ] `make test` runs ≥ 1 test per model in the schema package and the migration round‑trip.
- [ ] Schema parity test passes for all 67 models in `ALL_MODELS` (count from the source file).
- [ ] `client_message_json_schema()` validates the three sample lines in UX §2.2/§7.1 when hand‑written as JSON, and rejects an unknown `action`.
- [ ] `tt-admin seed demo` creates exactly 42 items, 4 projects, 8 people, 7 epics, 4 milestones, 4 risks (counts match `gen_snapshot.py` output) with `WEB-101 … GOV-125` keys.
- [ ] Writing a row through `UnitOfWork` without appending an `Event` fails a unit test guard (`uow.commit()` raises `MissingEventError` when projection tables changed but the buffer is empty — allow‑list the seed path).
- [ ] CI green.

## Exit checklist
- [ ] `docs/api/openapi.json` committed and diff‑checked in CI.
- [ ] `docs/api/event-catalogue.md` exists with the appendix A starter list.
- [ ] `packages/schema/README.md` written.
- [ ] ADR‑0001 "Transactional projection instead of replay event sourcing" written from §4.1 of the architecture doc.

## Handover notes for Phase 1
Phase 1 will add repositories/services per bounded context. Keep `tt_server/db/models/*.py` free of business logic. The only place that may `INSERT` into `events` is `EventWriter`.
