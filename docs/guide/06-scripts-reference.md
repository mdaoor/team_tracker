# 6. Scripts reference

All scripts are bash, live in [`scripts/`](../../scripts), read `.env` (without overriding variables already set in your shell),
and print help with `--help`. Use them through the dispatcher **`scripts/ttctl <command>`** or call a script directly.

```text
scripts/
├── ttctl                     dispatcher: one entry point for every action below
├── setup.sh                  install dependencies, write .env, build, (create + seed DB)
├── doctor.sh                 check tools, config, database, processes
├── start.sh                  start components in the background (or one in the foreground)
├── stop.sh                   stop components
├── restart.sh                stop + start
├── status.sh                 table / JSON of components, PIDs, ports, health
├── logs.sh                   show / follow / clear logs
├── db.sh                     database: start stop status create migrate seed setup reset drop backup restore psql verify
├── docker.sh                 docker compose wrapper
├── client.sh                 tt CLI: install, wheels, dev token/login, run, logout
├── build.sh                  release builds: presentation web desktop desktop-native client images openapi
├── check.sh                  lint, typecheck, tests, e2e, format
├── clean.sh                  remove build output, caches, logs (and deps)
├── lib/common.sh             shared helpers (paths, .env loading, PIDs, health waits)
├── prod/install.sh           production install on a systemd host
├── prod/service.sh           production start/stop/status/logs/reload/health
├── prod/upgrade.sh           production upgrade with backup
└── repo/split-internal-docs.sh + internal-paths.txt   publish internal plans to a private repo
```
Runtime state: `.run/<component>.pid`, `.run/logs/<component>.log` (both ignored by git). Backups: `backups/` (ignored by git).

## Command table

| `scripts/ttctl ...` | Script | What it does |
| --- | --- | --- |
| `setup [--with-db] [--no-seed] [--browsers] [--prod]` | `setup.sh` | tools check, `.env`, `uv sync`, `pnpm install`, deck viewer build; `--with-db` creates/migrates/seeds; `--prod` = no dev deps, random secrets, web build |
| `doctor [--tools-only]` | `doctor.sh` | versions, `.env`, deps, DB reachability and migration revision, process table; exit 1 when something required is missing |
| `start [COMPONENT...]` | `start.sh` | default `server worker web`; optional `gateway desktop slack`; health-checked |
| `run COMPONENT` | `start.sh --foreground` | one component attached to the terminal |
| `stop [COMPONENT...] [--with-db]` | `stop.sh` | default: everything that runs; SIGTERM then SIGKILL after `TT_STOP_TIMEOUT` |
| `restart [COMPONENT...]` | `restart.sh` | stop + start |
| `status [--json]` | `status.sh` | exit 0 only if server, worker and web run |
| `logs [-f] [-n N] [COMPONENT...] \| --clear` | `logs.sh` | tail / follow; all logs when no component is named |
| `db start\|stop\|status` | `db.sh` | local Postgres service (systemd, service, pg_ctlcluster, Homebrew) or the compose `db` with `TT_DB_MODE=docker` |
| `db create` | `db.sh` | role + database from `TT_DATABASE_URL` + extensions (uses `sudo -u postgres`, or `TT_DB_ADMIN_URL`) |
| `db migrate` | `db.sh` | `tt-admin migrate` |
| `db seed [--reset]` | `db.sh` | `tt-admin seed demo` |
| `db setup` | `db.sh` | start + create + migrate + seed |
| `db reset [--yes]` / `db drop [--yes]` | `db.sh` | destructive, asks first; refuses while server/worker run |
| `db backup [FILE]` / `db restore FILE [--yes]` | `db.sh` | `pg_dump -Fc` / `pg_restore` |
| `db psql [ARGS]` / `db verify [WS]` | `db.sh` | SQL shell / `tt-admin verify-projection` |
| `docker up [--llm] [--tls] \| down \| stop \| start \| restart [SVC] \| ps \| logs [-f] [SVC] \| seed \| migrate \| shell [SVC] \| psql \| reset` | `docker.sh` | compose wrapper; `up` waits for API and web |
| `client install \| uninstall \| wheels [DIR] \| token [@h] [ws] \| login [@h] [ws] \| run ARGS \| logout` | `client.sh` | `token`/`login` need `TT_DEV_AUTH=1` |
| `build [TARGET...]` | `build.sh` | default `presentation web desktop client` |
| `check [all\|lint\|typecheck\|test\|quick\|e2e\|fmt]` | `check.sh` | tests need `TT_TEST_DATABASE_URL` or Docker (testcontainers) |
| `clean [--deps]` | `clean.sh` | never touches `.env`, the database or `backups/` |
| `prod install --mode proxy\|tls [--with-gateway] [--nginx] [--no-start]` | `prod/install.sh` | root; idempotent |
| `prod start\|stop\|restart\|status\|reload\|logs [-f] [UNIT]\|health [URL]\|enable\|disable` | `prod/service.sh` | systemd units `tt-server`, `tt-worker`, `tt-llm-gateway` |
| `prod upgrade [REF] [--no-backup]` | `prod/upgrade.sh` | backup, pull, deps, build, migrate, restart |
| `repo split --repo OWNER/NAME [--from REF] [--history] [--no-protect] [--dry-run]` | `repo/split-internal-docs.sh` | see [Repositories](09-repositories.md) |

## Make targets
The older `Makefile` targets still work and are what CI calls: `make dev` (docker compose up), `make down`, `make migrate`,
`make seed`, `make check`, `make lint`, `make typecheck`, `make test`, `make e2e`, `make fmt`, `make openapi`, `make presentation`,
plus shortcuts to the scripts: `make setup`, `make start`, `make stop`, `make status`, `make logs`.

## Using the scripts from automation
* Every script exits non-zero on failure; destructive ones accept `--yes`.
* `scripts/ttctl status --json` and `scripts/ttctl doctor` are safe to run from monitoring.
* `NO_COLOR=1` turns colours off; output is plain when not on a terminal.
* `TT_RUN_DIR` moves `.run/` (PIDs + logs) somewhere else.
