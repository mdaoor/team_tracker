# 3. Run and stop

This page is for a local or single-host install made with `scripts/ttctl setup`. For Docker use `scripts/ttctl docker ...`
([install B](02-install.md#b-docker-install)); for a systemd production server use `scripts/ttctl prod ...` ([Production](04-production.md)).

## The everyday commands

| I want to... | Command |
| --- | --- |
| start the app (server + worker + web) | `scripts/ttctl start` |
| start only some parts | `scripts/ttctl start server worker` |
| start optional parts | `scripts/ttctl start gateway` / `desktop` / `slack` |
| see what runs | `scripts/ttctl status` |
| stop everything | `scripts/ttctl stop` |
| stop one part | `scripts/ttctl stop web` |
| stop everything and the database | `scripts/ttctl stop --with-db` |
| restart after changing `.env` | `scripts/ttctl restart` |
| restart just the API | `scripts/ttctl restart server` |
| read logs | `scripts/ttctl logs server` |
| follow all logs live | `scripts/ttctl logs -f` |
| run one part in the terminal (Ctrl-C stops it) | `scripts/ttctl run server` |
| check everything | `scripts/ttctl doctor` |

## Start

```bash
scripts/ttctl start
```
For each component the script:
1. skips it if it is already running (PID in `.run/<component>.pid`);
2. refuses to start if its port is taken by another program, and tells you which port;
3. checks what it needs: Postgres reachable for `server`/`worker`, the deck viewer built for `web`, Slack tokens for `slack`;
4. starts it in the background in its own process group, writing to `.run/logs/<component>.log`;
5. waits until it answers its health URL (`/readyz` for the API, the page for web/desktop, `/healthz` for the gateway).
   If it does not, it prints the last 25 log lines and stops it again.

Order: **server -> worker -> web** (the worker needs the API's database schema; the web console proxies to the API).

Start the database first if it is not running:
```bash
scripts/ttctl db start
```

Example:
```text
$ scripts/ttctl start
 ok server started (pid 2106) - http://127.0.0.1:8000
 ok worker started (pid 2166) - log: .run/logs/worker.log
 ok web started (pid 2235) - http://127.0.0.1:5173/

Open http://localhost:5173/login   (dev: 'Development sign-in', workspace acme, handle @mahmoud)
```

## Status

```bash
scripts/ttctl status
```
```text
COMPONENT STATE     PID      PORT   HEALTH
server    running   2106     8000   healthy
worker    running   2166     -      -
web       running   2235     5173   healthy
gateway   stopped   -        8700   -
desktop   stopped   -        1420   -
slack     stopped   -        -      -
database  up        -        5432   localhost
```
`external` in STATE means the port is used by something not started by these scripts (an old process, Docker, another app).
`scripts/ttctl status --json` prints the same for monitoring. The exit code is 0 only when server, worker and web all run.

Health endpoints you can also call yourself:
```bash
curl -s localhost:8000/healthz
```
```bash
curl -s localhost:8000/readyz
```
`/healthz` = the process is alive; `/readyz` = it can reach the database and the job queue (`{"status":"ready",...}`).

## Stop

```bash
scripts/ttctl stop
```
Stops clients first and the API last. Each process (and its children: `uv`, `pnpm`, Vite, uvicorn reloader) gets SIGTERM,
then SIGKILL after 15 seconds (`TT_STOP_TIMEOUT`). The worker finishes its current job during that time.

Stop the database as well (local service, or the `db` container with `TT_DB_MODE=docker`):
```bash
scripts/ttctl stop --with-db
```

## Logs

```bash
scripts/ttctl logs server
```
```bash
scripts/ttctl logs -f worker web
```
```bash
scripts/ttctl logs -n 500 server
```
Logs are in `.run/logs/`. Every start/stop writes a `=== <time> start: <command>` marker. The server logs JSON when
`TT_LOG_JSON=true`. Delete old logs with `scripts/ttctl logs --clear` (everything must be stopped).

## Database actions

| Action | Command |
| --- | --- |
| start / stop / status | `scripts/ttctl db start`, `db stop`, `db status` |
| create role + database | `scripts/ttctl db create` |
| apply migrations (after `git pull`) | `scripts/ttctl db migrate` |
| load the demo workspace | `scripts/ttctl db seed` (`--reset` to recreate it) |
| wipe and recreate with demo data | `scripts/ttctl db reset` |
| backup / restore | `scripts/ttctl db backup` -> `backups/tt-<time>.dump`; `scripts/ttctl db restore backups/<file>` |
| SQL shell | `scripts/ttctl db psql` |
| check tables match the event log | `scripts/ttctl db verify acme` |

Destructive commands (`reset`, `drop`, `restore`) ask first; add `--yes` in scripts.

## After `git pull`

```bash
scripts/ttctl setup
```
```bash
scripts/ttctl db migrate
```
```bash
scripts/ttctl restart
```

## Settings that change how things run

Put these in `.env` or in front of the command (`TT_PORT=9000 scripts/ttctl start`):

| Variable | Default | Effect |
| --- | --- | --- |
| `TT_HOST` | `127.0.0.1` | bind address of the API. `0.0.0.0` to reach it from other machines |
| `TT_PORT` | `8000` | API port (the web console proxies to it) |
| `TT_WEB_PORT` | `5173` | web console port |
| `TT_GATEWAY_PORT` | `8700` | LLM gateway port |
| `TT_GATEWAY_MODE` | `local` | `local` = `--local-only` (Ollama, nothing leaves the host); `config` = use `TT_LLM_CONFIG` YAML |
| `TT_RELOAD` | `1` | `1` = API reloads on code change; `0` = no reload |
| `TT_START_TIMEOUT` | `90` | seconds to wait for a component to become healthy |
| `TT_STOP_TIMEOUT` | `15` | seconds before a stop becomes SIGKILL |
| `TT_DB_MODE` | `local` | `docker` = `db` commands use the compose `db` container |
