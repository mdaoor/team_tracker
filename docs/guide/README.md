# Team Tracker - Setup, Install, Run and Stop Guide

This guide covers installing, running, stopping and operating Team Tracker: the **server side** (API, worker, database,
web console, LLM gateway) and the **client side** (the `tt` command line, desktop palette, Mobile Lite, Slack bot).
Every action has a script in [`scripts/`](../../scripts). The single entry point is **`scripts/ttctl`**.

| # | Page | Read it when you want to... |
| --- | --- | --- |
| 1 | [Requirements](01-requirements.md) | check what to install first (Linux, macOS, Windows/WSL) |
| 2 | [Install](02-install.md) | install on a laptop or a test box, with or without Docker |
| 3 | [Run and stop](03-run-and-stop.md) | start, stop, restart, check and read logs for each component |
| 4 | [Production](04-production.md) | deploy on a Linux server (systemd + nginx), Docker images or Kubernetes, upgrade, back up |
| 5 | [Clients](05-clients.md) | install and use the `tt` CLI, desktop palette, Mobile Lite, Slack bot, LLM gateway |
| 6 | [Scripts reference](06-scripts-reference.md) | look up every `ttctl` command and option |
| 7 | [Configuration](07-configuration.md) | understand `.env` and the important settings |
| 8 | [Troubleshooting](08-troubleshooting.md) | fix a problem |
| 9 | [Repositories](09-repositories.md) | understand this repo vs the private planning repo |

## Five-minute quick start (laptop, no Docker)

You need Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm, and PostgreSQL 16 ([details](01-requirements.md)).

```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```
```bash
scripts/ttctl setup --with-db
```
```bash
scripts/ttctl start
```
Then open <http://localhost:5173/login> and click **Development sign-in** (workspace `acme`, handle `@mahmoud`).

Check and stop:
```bash
scripts/ttctl status
```
```bash
scripts/ttctl stop
```

## Quick start with Docker
```bash
scripts/ttctl docker up
```
```bash
scripts/ttctl docker seed
```
Open <http://localhost:5173/login>. Stop with `scripts/ttctl docker down`.

## What runs where

```text
             browser ──► web console (:5173 dev / nginx in prod) ──┐
 tt CLI / desktop / Slack ─────────────────────────────────────────┼──► API server (:8000) ──► PostgreSQL 16 (:5432)
                         └──► LLM gateway (:8700, optional) ◄──────┘          ▲
                                     └──► Ollama / cloud model                │
                                                                  worker (background jobs)
```

| Component | What it does | Dev command behind `ttctl start` | Port |
| --- | --- | --- | --- |
| `server` | REST API, sign-in, exports, deck viewer | `uvicorn tt_server.main:create_app --factory` | 8000 |
| `worker` | background jobs: daily logs, governance, recurrence, Jira, exports | `python -m tt_worker` | - |
| `web` | manager/admin web console (React + Vite) | `pnpm --filter @tt/web dev` | 5173 |
| `gateway` | LLM gateway (optional): free-text understanding, insights, suggestions | `tt-llm serve --local-only` | 8700 |
| `desktop` | desktop palette / personal tracker preview in a browser (optional) | `pnpm --filter @tt/desktop dev` | 1420 |
| `slack` | Slack bot (optional) | `tt-slack` | - |
| database | PostgreSQL 16 with `pg_trgm`, `ltree`, `btree_gist` | your OS service or the `db` container | 5432 |

## Other documentation in this repository
* Using the product: [reporter](../guides/reporter.md), [manager](../guides/manager.md), [admin](../guides/admin.md) guides.
* Acceptance testing: [UAT guide](../uat/README.md), [UAT script](../uat/uat-script.md), [known limitations](../uat/known-limitations.md).
* Operations: [configuration reference](../ops/config-reference.md), [Helm](../ops/helm.md), [runbooks](../runbooks/README.md), [security](../security/README.md).
* Clients and integrations: [`docs/client/`](../client), [LLM gateway](../llm/gateway.md), [Jira](../integrations/jira.md).
* API: [`docs/api/`](../api) (OpenAPI at `/docs` on a running server).
