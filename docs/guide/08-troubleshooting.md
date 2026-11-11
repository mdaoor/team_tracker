# 8. Troubleshooting

Start with:
```bash
scripts/ttctl doctor
```
```bash
scripts/ttctl logs -n 200 server
```

| Symptom | Cause | Fix |
| --- | --- | --- |
| `'uv' is not installed` / `'pnpm' ...` | tool missing | [Requirements](01-requirements.md) |
| `dependencies are not installed - run scripts/setup.sh first` | no `.venv` / `node_modules` | `scripts/ttctl setup` |
| `Postgres is not reachable on localhost:5432` | database not running | `scripts/ttctl db start` (or start it your way; `pg_isready` to check) |
| `password authentication failed for user "tt"` | role missing or another password | `scripts/ttctl db create`, or fix `TT_DATABASE_URL` |
| `permission denied to create extension` | the app role is not a superuser | `scripts/ttctl db create` (creates the extensions as superuser), or run the three `CREATE EXTENSION` lines as `postgres` |
| `relation ... does not exist` | not migrated | `scripts/ttctl db migrate` |
| `port 8000 is already in use by another program` | old process, Docker stack, other app | `scripts/ttctl status` (STATE `external`); stop it (`scripts/ttctl docker down`, or find it with `ss -ltnp \| grep 8000`), or set `TT_PORT` |
| `server did not become healthy` | crash on start | the script prints the last 25 log lines; usually database or `.env` (see next rows) |
| server exits: `TT_SESSION_SECRET ...` / `TT_DEV_AUTH ...` / `TT_SECRET_KEY ...` | `TT_ENV` is not `dev` and a secret is weak or dev sign-in is on | set real secrets ([Configuration](07-configuration.md)) or `TT_ENV=dev` on a laptop |
| web console says **network error** | API not on the port the web dev server proxies to | `scripts/ttctl start server`; if the API is elsewhere set `TT_API_URL` and `scripts/ttctl restart web` |
| no **Development sign-in** button | `TT_DEV_AUTH` is off | set `TT_DEV_AUTH=1` in `.env` (dev only), `scripts/ttctl restart server` |
| 401 on every call | session expired (12 h) or token revoked | sign in again; `tt login ...` for the CLI |
| PDF / PNG export answers 503 | deck viewer or Chromium missing | `scripts/ttctl build presentation`, `scripts/ttctl setup --browsers` |
| `tt: command not found` after install | uv tool dir not on `PATH` | `uv tool update-shell`, open a new terminal |
| `tt login` fails with a certificate error | internal CA | `tt login ... --ca-bundle /path/ca.pem` (TLS checks cannot be skipped) |
| captured lines are not understood | no LLM gateway, free text beyond the parser | use the documented syntax ([reporter guide](../guides/reporter.md)) or run the gateway ([Clients](05-clients.md#llm-gateway)) |
| `gateway did not become healthy` | Ollama not running / model not pulled, or bad YAML | `ollama serve`, `ollama pull qwen2.5:7b-instruct`; `scripts/ttctl logs gateway` |
| `slack: SLACK_BOT_TOKEN is not set` | Slack settings missing | add them to `.env` ([Clients](05-clients.md#slack-bot)) |
| `the Docker daemon is not running` | Docker not started | start Docker Desktop / `sudo systemctl start docker` |
| Docker web container slow on first start | it runs `pnpm install` | wait; `scripts/ttctl docker logs -f web` |
| `db restore` fails with `must be owner of extension` | old dump restored with an older script | update the repo; the script restores with `--no-owner --no-comments` |
| a stop hangs | a job is still running | it is SIGKILLed after `TT_STOP_TIMEOUT` (15 s) |
| stale PID after a reboot | process gone, file left | harmless: scripts check the PID is alive and clean up |

## Production
* `scripts/ttctl prod status` and `scripts/ttctl prod logs -f`.
* Job backlog, Jira push failures, LLM outage, restore: [runbooks](../runbooks/README.md).
* Certificate rotation: replace the PEM files, `scripts/ttctl prod reload` (or wait 30 s for the file check).

## Getting help
Collect `scripts/ttctl doctor` output, `scripts/ttctl status --json` and the relevant log lines, remove secrets, and open an issue.
