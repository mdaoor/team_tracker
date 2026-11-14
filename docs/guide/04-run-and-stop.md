# 4. Run, stop, restart, status, logs

For a local or single-host install (`ttctl setup` or a bundle). Production with systemd: `ttctl prod ...`
([Production](06-production.md)); Docker: `ttctl docker ...`.

| I want to... | Command |
| --- | --- |
| start server + worker + web | `scripts/ttctl start` |
| start some parts / optional parts | `scripts/ttctl start server worker` · `start gateway` · `start desktop` · `start slack` |
| see what runs | `scripts/ttctl status` (`--json` for monitoring) |
| stop everything | `scripts/ttctl stop` (`--with-db` also stops PostgreSQL) |
| restart after editing `.env` | `scripts/ttctl restart` |
| read / follow logs | `scripts/ttctl logs server` · `scripts/ttctl logs -f` |
| run one part in the terminal | `scripts/ttctl run server` (Ctrl-C stops it) |
| see what a command would do | add `--dry-run` |
| see every command and probe | add `-v` (`-vv` traces the shell) |
| check the whole install | `scripts/ttctl doctor` |

## Start
```bash runnable
scripts/ttctl start
```
For each component: skips it if already running; refuses a port another program holds (`TT-E300`); checks what it needs
(database reachable, web console built or Node present, Slack tokens); starts it in the background in its own process
group with output to `.run/logs/<component>.log`; waits for its health URL. If it does not become healthy it prints the
last 25 log lines, stops it again and exits with `TT-E301`.

The web console runs with Vite when Node and `node_modules` are present, otherwise from the prebuilt `apps/web/dist`
through a small built-in server that also forwards the API paths (`TT_WEB_MODE=vite|static` to force one).

## Status
```bash runnable
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
database  up        -        5432   localhost (native)
```
`external` = the port is used by a program ttctl did not start. Exit code 0 only when server, worker and web run.
Health endpoints: `/healthz` (process alive) and `/readyz` (database and job queue reachable).

## Stop and restart
```bash runnable
scripts/ttctl stop
```
Clients first, the API last; SIGTERM, then SIGKILL after `TT_STOP_TIMEOUT` (15 s). `restart` = stop + start.

## Logs
```bash
scripts/ttctl logs -f worker web
```
* Component output: `.run/logs/<component>.log` (a `=== <time> start: <command>` line marks each start).
* Every ttctl run also writes a full-detail log `.run/logs/ttctl-<command>-<time>.log`, verbose or not; its path is printed
  with every error. `scripts/ttctl logs --runs` lists them.
* To send logs out of a closed site: `scripts/ttctl support-bundle` (masked configuration, doctor output, logs).

## Global options (every command)
`-v` / `-vv` / `-q` / `--dry-run` / `-y` / `--no-color` / `--log-file PATH`, also as environment variables
(`TT_VERBOSE=1`, `TT_DRY_RUN=1`, `TT_YES=1`). Exit codes: 0 ok, 1 runtime failure, 2 usage, 3 prerequisite missing,
4 blocked because offline, 5 configuration. Full list: [Scripts reference](10-scripts-reference.md).

## After `git pull`
```bash
scripts/ttctl setup && scripts/ttctl db migrate && scripts/ttctl restart
```
