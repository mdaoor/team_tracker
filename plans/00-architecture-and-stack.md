# 00 — Architecture and technology stack

Status: **decided for this implementation**. Deviations require an ADR (appendix C).

## 1. Guiding constraints taken from the spec

| constraint | source | consequence |
|---|---|---|
| One canonical store, many views | object model §1 | Single relational DB; views are queries, never copies |
| Append‑only `Event` log is the source of truth; tables are projections | §1 (2) | Every write = `Event` row + projection update in one transaction (see §4 below) |
| Reference models are Python dataclasses | `team_tracker_models.py` | Backend in Python so the schema package is the models file, ported 1:1 |
| Policies are CEL, type‑checked on save, evaluated per event and on schedule | §8 | Need a CEL runtime in the backend language; `cel-python` exists |
| Local LLM on the reporter's device emits `ClientBatch` via JSON‑schema constrained decoding | UX §2.4 + product requirement for an independent, agentic LLM server | Client talks to the LLM gateway (which may run locally); gateway talks to Ollama / cloud via LiteLLM; core server is model‑agnostic |
| Presentation page is a single self‑contained HTML file with no external calls | UX §5 | Keep it vanilla JS; serve from the API with the snapshot inlined or fetched same‑origin |
| Methodology is configuration; any state → any state | §1 (3) | No hard‑coded workflows; everything is rows |
| Privacy from day one | §1 (8), §10 | ACL is a core module, not middleware bolted on later |

## 2. Stack

| layer | choice | why | alternatives rejected |
|---|---|---|---|
| Language (server) | **Python 3.12** | Matches the normative models file; `cel-python`; rich LLM tooling | TypeScript (would require re‑deriving the schema; CEL support weaker) |
| Web framework | **FastAPI** + Uvicorn | Pydantic v2 native; OpenAPI for free (the client SDKs and the LLM translator schema come from it) | Django (ORM too opinionated for the generic dimension/classification model), Flask (no typing) |
| Validation / DTOs | **Pydantic v2** | `to_json_schema()` stub in the models file says "swap for pydantic" | — |
| ORM / migrations | **SQLAlchemy 2.0 (typed, async)** + **Alembic** | Mature, explicit SQL when needed (filter grammar compiles to SQL) | Tortoise, Prisma‑py |
| Database | **PostgreSQL 16** with `pg_trgm`, `ltree`, `btree_gist` | Trigram fuzzy match for titles; ltree for `WorkItem.path`; JSONB for `settings`, `payload`, `filter`; range exclusion for placements | SQLite (no trigram/ltree; fine for tests only — do **not** use it even in tests, use Postgres via testcontainers) |
| Background jobs | **Procrastinate** (Postgres‑backed queue) | No Redis to operate; transactional enqueue in the same DB tx as the `Event` | Celery+Redis, APScheduler (no persistence) |
| CEL | **`cel-python`** (`celpy`) | Only maintained Python CEL; supports custom functions and type env | Rego/OPA (rejected in spec), JSON‑Logic (rejected in spec) |
| Cache | none in v1; Postgres materialised views for `MetricSnapshot` | Simplicity | Redis |
| Auth | **OIDC** (Entra ID / ADFS / any provider) **or Active Directory via LDAPS** (service‑account bind, group → role mapping), optional **Kerberos/SPNEGO SSO** for domain‑joined browsers; **personal API tokens** for clients; sessions via signed cookies | Product requirement: work with Windows NT accounts and NT groups; clients are tray apps/CLIs | Local passwords (no requirement) |
| TLS | **HTTPS everywhere**: Uvicorn with `--ssl-certfile/--ssl-keyfile` (PEM) for single‑node Linux, or Caddy/nginx termination with the same PEM pair; internal CA bundle (PEM) distributed to CLI/desktop (`TT_CA_BUNDLE`) and used for LDAPS | Requirement: PEM‑based HTTPS on Linux | Self‑signed without CA distribution |
| Frontend (console + views) | **React 18 + TypeScript + Vite**, TanStack Query/Table/Router, Radix primitives, CSS modules with the token set from the prototype's `:root` | Component‑heavy editors (tree, pipeline, matrix) | Vanilla (too much state), Svelte (fewer agents know it) |
| Presentation page | **Vanilla HTML/CSS/JS, single file** generated from the prototype | Spec requirement: self‑contained, print‑friendly | Bundling it into React |
| CLI | **Typer** + `httpx` | Same language as the server; shares the schema package | — |
| LLM gateway | **Standalone service `apps/llm_gateway` on LiteLLM** (Router, aliases, fallbacks, budgets, structured output); own agent loop with tool access to the core API; deployable as laptop sidecar, team box or cloud | All LLM logic in one independent place; clients and core server stay model‑agnostic; privacy modes (`local_only | hybrid | cloud`) | Embedding Ollama calls in the client and provider SDKs in the server (two code paths, no shared evals) |
| Local models | **Ollama** (default) or llama.cpp behind the gateway; `qwen2.5:7b-instruct` for translate, `qwen2.5:14b-instruct` for agent tasks | JSON‑schema constrained decoding; laptop GPU/CPU | — |
| Cloud models | Anthropic Claude (`claude-sonnet-5-5` summariser, `claude-opus-5-5` reasoner) via LiteLLM, only in `hybrid|cloud` modes | Narrative quality; swappable by config | — |
| Desktop shell | **Tauri v2** wrapping the React palette | Small binary, global hotkey, tray | Electron (size) |
| Chat bot | Slack Bolt (Python) first; Teams later | Reporter persona lives in Slack | — |
| Management commands (P14) | Command registry in `apps/server/tt_server/api/commands` (JSON Schema per op from Pydantic, per-op permission, token scope and preview/destructive flags); client forms and the gateway translator are generated from `GET /commands/catalogue` (`jsonschema` validation on the client) | One contract for CLI, desktop, Slack and the LLM; ACL and events identical to the web console | A second RPC API (drift from the console) |
| Personal tracker (P15) | Local-first store in `tt_client.personal` (Fernet-encrypted rows, key in the OS keyring via `keyring` with a 0600 file fallback), `python-dateutil` RRULE for recurrence, Google Calendar and Microsoft Graph adapters behind one interface, `cryptography` | Private by default, nothing leaves the device without a send | SQLCipher (build burden), server-side planner (privacy) |
| Sidecar (P14/P15) | `tt-sidecar` console script (`apps/cli/tt_cli/sidecar.py`) bundled with PyInstaller for the Tauri shell | The desktop palette reuses the Python client core | Re-implementing the client core in Rust |
| Tests | **pytest** + **testcontainers‑postgres** + **Hypothesis**; **Vitest** + **Playwright** for web | — | — |
| Lint/format | **Ruff** (lint+format), **mypy --strict** on `packages/schema` and `apps/server`; **ESLint + Prettier** for web | — | — |
| Packaging | **uv** workspaces (monorepo), **pnpm** workspaces for web | Fast, lockfile‑driven | poetry |
| Containers / deploy | Docker Compose for dev; one `server` image + `worker` image + Postgres; Helm chart optional in P12 | — | — |
| Observability | OpenTelemetry (traces+metrics) → OTLP; structlog JSON logs | — | — |

## 3. Repository layout (monorepo)

```
team_tracker/
├── docs/
│   ├── spec/                      # normative design docs (do not edit; propose changes via ADR)
│   ├── decisions/                 # ADR-0001-....md
│   └── api/                       # generated OpenAPI snapshot, event catalogue
├── plans/                         # this plan set
├── packages/
│   ├── schema/                    # tt_schema: enums + pydantic models ported from team_tracker_models.py
│   │                              #   also exports JSON Schema for ClientMessage/ClientBatch
│   ├── filter_grammar/            # tt_filter: SavedView/candidate_filter JSON → SQLAlchemy
│   ├── cel_policies/              # tt_cel: CEL env, context schema versions, Tier-3 functions, templates
│   └── client_core/               # tt_client: regex parser, ContextPack cache, offline queue, gateway translator client
├── apps/
│   ├── server/                    # FastAPI app: tt_server
│   │   ├── tt_server/
│   │   │   ├── main.py            # app factory
│   │   │   ├── db/                # engine, session, base, migrations (alembic/)
│   │   │   ├── domain/            # one package per bounded context (see §5)
│   │   │   ├── api/               # routers, DTOs; thin
│   │   │   ├── events/            # Event writer, event catalogue, outbox → jobs
│   │   │   ├── acl/               # permission checks, visibility filters
│   │   │   ├── jobs/              # procrastinate tasks (propagation, policies, habits, snapshots)
│   │   │   └── integrations/      # jira/, llm_gateway/ (thin client), slack/, ldap/
│   │   └── tests/
│   ├── worker/                    # entrypoint for procrastinate worker (imports tt_server.jobs)
│   ├── cli/                       # tt CLI (Typer)
│   ├── web/                       # React console + views
│   ├── presentation/              # single-file presentation page + build script
│   ├── desktop/                   # Tauri shell (P11)
│   ├── slackbot/                  # (P11)
│   └── llm_gateway/               # tt_llm: LiteLLM routing + agent loop + task endpoints (P2b)
├── tools/
│   ├── gen_snapshot.py            # sample snapshot generator (from spec)
│   └── seed/                      # demo workspace seed (uses gen_snapshot data)
├── docker-compose.yml
├── pyproject.toml                 # uv workspace root
├── package.json                   # pnpm workspace root
└── Makefile                       # make dev | test | lint | migrate | seed
```

## 4. Core architectural patterns

### 4.1 Event‑first writes (the "transactional projection" pattern)
We do **not** implement replay‑based event sourcing in v1. We implement the stronger invariant that the spec actually needs: every state change is recorded as an `Event` row **in the same database transaction** as the projection tables it changes. Concretely, every domain service method has the shape:

```python
async def move_stage(self, uow: UnitOfWork, actor_id, target, stage_id, reason, source=EventSource.OVERLAY):
    async with uow:  # one transaction
        placement = ...  # close old StagePlacement, open new
        uow.events.append(
            Event(
                actor_id=actor_id,
                entity_type=TargetType.STAGE_PLACEMENT,
                entity_id=placement.id,
                event_type="stage.moved",
                payload={...},
                source=source,
            )
        )
        await uow.commit()  # commit also enqueues follow-up jobs (outbox)
```

Rules:
- `Event.event_type` is dot‑namespaced and listed in `docs/api/event-catalogue.md` (appendix A has the initial list). Adding a type means adding it there.
- `Event.source` must be set explicitly; the default `client` is only correct in the ingestion path.
- `Event.correlation_id` links a `ClientBatch` message to everything it caused (item created → activity started → propagation → policy fired).
- Follow‑up work (propagation, policy evaluation, Jira push) is enqueued with Procrastinate **inside the same transaction** (Procrastinate uses the same Postgres connection), so no event is ever processed without having been committed, and none is lost.
- A `rebuild_projection(entity_type)` admin command is a P12 stretch goal; design tables so it would be possible (no business state that exists only outside `Event.payload`).

### 4.2 Two layers enforced in code, not by convention
- `domain/capture/` owns `WorkItem` creation with title only, `Activity`, `Evidence`, `TimeEntry`, `DailyLog`. Its services accept `ClientMessage`s and **cannot** fail on missing context.
- `domain/overlay/` owns everything else. Its repositories are forbidden (by a unit test that introspects SQLAlchemy update statements in CI) from updating columns tagged `layer="capture"` in the schema package metadata.
- The schema package annotates each `WorkItem` field with `layer: capture | overlay | system | either` taken from object model §7 table.

### 4.3 Dimensions as the universal query path
`WorkItem.project_id`, current stage, team etc. remain real columns/FKs for performance, but the server **mirrors** them into `Classification` rows (`source=rule`, `dimension.kind=builtin`). All filtering in the view layer goes through `Classification` + `DimensionValue`, so custom and builtin dimensions behave identically (object model §4, last paragraph; §5 `StagePlacement` paragraph).

### 4.4 Propagation engine
A single module `domain/overlay/propagation.py` implements the table in object model §6 ("Propagation rules"). It runs as a job after any event listed in its trigger table, writes `Event{source: propagation}` rows, and respects overrides (`priority_source=manual`, item `visibility=private`, child override). It also implements the **stage ↔ state sync** of §13 decision 1.

### 4.5 Governance evaluation
- `on_event` policies: the event outbox fans out to `jobs.evaluate_policies_for_event(event_id)`; the context builder loads only the one entity graph; CEL evaluates in‑process.
- `scheduled` policies: cron → `candidate_filter` (same grammar as `SavedView.filter`) compiled to SQL → batched CEL evaluation.
- Tier‑3 registered functions read from `MetricSnapshot` / indexed queries, never scan.

### 4.6 Multi‑tenancy
Row‑level: every table except `Person`, `Role` (builtin), `PolicyTemplate` (builtin), `HabitDefinition` (builtin) carries `workspace_id` directly or via an FK chain, and every repository query is scoped by the request's workspace. Enforced by a `WorkspaceScoped` mixin and a test that greps the models.

### 4.7 IDs and keys
- All ids: UUID v7 strings (`uuid_utils.uuid7()`; fall back to the `new_id()` shim in the models file).
- Human keys: `Project.key` (upper‑snake, unique per workspace), `WorkItem.key = f"{project.key}-{n}"` with a per‑project sequence row updated with `SELECT … FOR UPDATE`. Inbox items get `INBOX-<handle>-<n>` until moved; on move they get a key in the destination project and keep the old one in `external_refs["previous_key"]`.
- `WorkItem.path` is an ltree of ids (`root.child.grandchild`) maintained by trigger or service; used for subtree propagation and roll‑ups.

### 4.8 Time
Store UTC `timestamptz`. `Person.time_zone` drives day boundaries for `DailyLog`, "today", business‑day math in Tier‑3 functions. Dates (`planned_end`, `due_date`) are `date` columns, interpreted in the workspace's `settings.time_zone` unless the person's zone is relevant (daily log).

## 5. Bounded contexts (map to `apps/server/tt_server/domain/`)

| package | tables (from object model) | phase |
|---|---|---|
| `tenancy` | Person, Workspace, WorkspaceMember, Team, Membership, ReportingLine, ContextNote | 1 |
| `projects` | Project, ProjectMember, WorkItemType, Workflow, WorkflowState, Iteration, Milestone, Component, Release, RecurrenceRule, Approval, ApprovalDecision | 1 (core), 3 (approvals) |
| `items` | WorkItem, WorkItemLink, Assignment, Tag, TagLink, Estimate, Checklist, ChecklistItem, Comment, Attachment, CustomFieldDefinition, CustomFieldValue, Baseline | 1 |
| `capture` | Activity, Evidence, TimeEntry, DailyLog + ingestion | 1 |
| `events` | Event, outbox | 0/1 |
| `dimensions` | Dimension, DimensionValue, Classification | 3 |
| `process` | Process, Stage, StagePlacement, StageTransitionRule | 3 |
| `products` | Product, ProductLink, Objective, KeyResult, WeightScheme, Weight | 3 |
| `risks` | Risk, RiskLink, RiskAction | 3 |
| `acl` | Role, RoleAssignment, AccessGrant + visibility resolver | 4 |
| `views` | SavedView + filter compiler + layout queries | 6 |
| `snapshot` | snapshot assembler, Insight | 7 |
| `governance` | PolicyTemplate, Policy, PolicyTestRun, Violation, HabitDefinition, Habit, MetricSnapshot | 8 |
| `integrations.jira` | mapping + webhooks | 10 |

## 6. Environments

- `make dev` → docker compose: postgres, server (reload), worker, web (vite), llm-gateway, ollama (optional profile), caddy (TLS, optional profile).
- Config via env vars with a `Settings` pydantic model; `.env.example` lists all.
- Feature flags (`settings.features`): `insights_llm`, `jira_sync`, `habits_nightly`, `slack_bot`. Default off except in demo seed.

## 7. Security baseline (expanded in P12)

- All API routes require auth except `/healthz`, `/readyz`, `/auth/*`.
- Personal API tokens are hashed (argon2) at rest; shown once.
- `Event.raw_input` (the reporter's raw text) is PII‑adjacent: stored, but only readable by the actor and `line`/`senior` managers (P4 rule), never exported in snapshots.
- CEL is sandboxed by construction; still cap evaluation cost (`celpy` interpreter step limit) and expression length (4 KB).
- No SQL is ever built from strings outside `packages/filter_grammar`, which is fuzz‑tested.
