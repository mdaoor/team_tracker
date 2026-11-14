# Team Tracker

Two-layer work tracking: reporters type one line (or fill a form), a local or independent LLM gateway structures it, managers
enrich it on the server, and everything is shown as views, slide decks and a CEL-governed policy engine.

Status: release candidate `1.0.0-rc1` ([CHANGELOG](CHANGELOG.md), [known limitations](docs/guide/15-known-limitations.md)).

## Get it running

```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```
```bash
scripts/ttctl setup --with-db
```
```bash
scripts/ttctl start
```
Open <http://localhost:5173/login>, click **Development sign-in** (workspace `acme`, handle `@mahmoud`).
Stop with `scripts/ttctl stop`; see what runs with `scripts/ttctl status`. With Docker instead: `scripts/ttctl docker up`.

Needs Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm, and PostgreSQL 16 - `scripts/ttctl doctor` checks.

## Documentation

**Setup, install, run and stop: [`docs/guide/`](docs/guide/README.md)**

| Topic | Where |
| --- | --- |
| Requirements, install (local / Docker), run and stop, production (systemd + nginx, Docker, Helm), clients, scripts, configuration, troubleshooting | [`docs/guide/`](docs/guide/README.md) |
| Every control script (`scripts/ttctl help`) | [scripts reference](docs/guide/06-scripts-reference.md) |
| Using the product | [reporter](docs/guides/reporter.md), [manager](docs/guides/manager.md), [admin](docs/guides/admin.md) guides |
| Acceptance testing | UAT guide, UAT script |
| Clients | [`tt` CLI and management commands](docs/client/management-commands.md), [desktop](docs/client/desktop.md), [personal tracker](docs/client/personal-tracker.md), [Slack](docs/client/slack.md) |
| Operations | [configuration reference](docs/ops/config-reference.md), [Helm](docs/ops/helm.md), [runbooks](docs/runbooks/README.md), [security](docs/security/README.md) |
| Integrations | [LLM gateway](docs/llm/gateway.md), [Jira](docs/integrations/jira.md), [presentation templates](docs/presentation/README.md) |
| API | [`docs/api/`](docs/api), OpenAPI at `/docs` on a running server |
| Architecture decisions and the normative spec | decisions (internal planning repository), spec (internal planning repository) |

Implementation plans, reviews and handover prompts are kept in a separate private repository
([Repositories](docs/guide/09-repositories.md)).

## Repository layout

| Path | What |
| --- | --- |
| `apps/server` | API (FastAPI), `tt-admin`, migrations |
| `apps/worker` | background jobs (Procrastinate) |
| `apps/web` | web console (React + Vite) |
| `apps/presentation` | single-file deck viewer |
| `apps/cli` | `tt` command line |
| `apps/desktop` | desktop palette, personal tracker, Mobile Lite (Tauri shell in `src-tauri`) |
| `apps/llm_gateway` | LLM gateway (`tt-llm`) |
| `apps/slack_bot` | Slack bot (`tt-slack`) |
| `packages/` | shared libraries: schema, client core, filter grammar, CEL policies |
| `scripts/` | install / run / stop / operate scripts (`scripts/ttctl`) |
| `deploy/` | systemd units, nginx config, Helm chart, backup scripts, TLS notes |
| `ops/` | Prometheus alerts, Grafana dashboard |
| `tools/` | generators (schemas, docs, snapshot) and the demo seed |

## Development
```bash
make check
```
Runs lint, type checks and tests (Python tests need an empty Postgres 16 database in `TT_TEST_DATABASE_URL`, or Docker for a
testcontainer). More: [scripts reference](docs/guide/06-scripts-reference.md#make-targets).
