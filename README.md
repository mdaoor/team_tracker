# Team Tracker

Two-layer work tracking: reporters type one line (or fill a form), a local or independent LLM gateway structures it, managers
enrich it on the server, and everything is shown as views, slide decks and a CEL-governed policy engine.

Status: release candidate `1.0.0-rc1` ([CHANGELOG](CHANGELOG.md), [known limitations](docs/guide/15-known-limitations.md)).

## Get it running

```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```
```bash
scripts/ttctl setup --with-db --install-missing
```
```bash
scripts/ttctl start
```
Open <http://localhost:5173/login>, click **Development sign-in** (workspace `acme`, handle `@mahmoud`).
`scripts/ttctl status` shows what runs, `scripts/ttctl stop` stops it. Missing tools are installed into `.tools/` without
root; system packages (PostgreSQL 16) are shown and asked for. Air-gapped hosts install from one bundle file.

## Documentation

**Everything about installing, running, upgrading and operating is in [`docs/guide/`](docs/guide/README.md)**:
requirements and support matrix, install online and air-gapped, run/stop/status/logs, database, production (systemd, TLS,
Active Directory), upgrade and rollback, clients (Windows/Linux/macOS, offline), configuration and scripts reference,
troubleshooting by error code, uninstall, FAQ.

Using the product: [reporter](docs/guides/reporter.md), [manager](docs/guides/manager.md), [admin](docs/guides/admin.md)
guides. API: [`docs/api/`](docs/api). Plans, reviews and design records live in a private repository
([why](docs/guide/14-repositories.md)).

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
| `scripts/` | install / run / stop / operate scripts (`scripts/ttctl`), air-gapped bundles, client kit |
| `deploy/` | systemd units, nginx config, Helm chart, backup scripts, TLS notes |
| `ops/` | Prometheus alerts, Grafana dashboard |
| `tools/` | generators (schemas, docs, snapshot) and the demo seed |
| `ci/ops/`, `tests/ops/` | container tests of install/run/air-gap, script tests |

## Development
```bash
make check
```
Runs lint, type checks and tests (Python tests need an empty Postgres 16 database in `TT_TEST_DATABASE_URL`, or Docker for a
testcontainer). Ops scripts: `scripts/ttctl check ops`. More: [scripts reference](docs/guide/10-scripts-reference.md).
