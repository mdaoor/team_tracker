# Install locally and start UAT

This is the path I verified on a clean checkout (Linux, no Docker): database, server, worker, web console, CLI. About 15 minutes.

## 0. What you need
| Tool | Version | Check |
| --- | --- | --- |
| Python + [uv](https://docs.astral.sh/uv/) | 3.12+ | `uv --version` |
| Node + pnpm | Node 22, pnpm 9+ (`corepack enable`) | `pnpm --version` |
| PostgreSQL | 16 (extensions `pg_trgm`, `ltree`, `btree_gist` ship with it) | `psql --version` |
| Chromium (only for PDF/PNG export and Playwright) | any recent | `pnpm --filter @tt/web exec playwright install chromium` |

Docker users can skip the database step: `make dev` starts Postgres, API (:8000), worker and web (:5173) with live reload.

## 1. Get the code and dependencies
```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
git checkout claude/listed-plan-implementation-hygguz        # the implementation branch (or the release branch you were given)
uv sync --all-packages                                        # Python workspace (server, worker, CLI, gateway, Slack bot)
pnpm install                                                  # web console, presentation bundle, desktop palette
make presentation                                             # builds the single-file deck viewer used by Present / PDF / PNG
```

## 2. Database
```bash
sudo -u postgres psql -c "CREATE USER tt WITH PASSWORD 'tt' CREATEDB" -c "CREATE DATABASE tt OWNER tt"
cp .env.example .env            # defaults already point at postgresql+psycopg://tt:tt@localhost:5432/tt and enable TT_DEV_AUTH=1
make migrate                    # alembic + the job-queue schema (creates the three extensions)
make seed                       # demo workspace "acme": 5 projects, 49 items, a plan-only project ERP, people, policies
```
If `make migrate` complains about extensions, run it once as a superuser or `CREATE EXTENSION pg_trgm; CREATE EXTENSION ltree; CREATE EXTENSION btree_gist;`
in the `tt` database first. Re-create the demo at any time with `make seed SEED_ARGS=--reset`.

## 3. Start the processes (three terminals)
```bash
uv run uvicorn tt_server.main:create_app --factory --reload --port 8000     # API
uv run python -m tt_worker                                                   # background jobs (daily logs, governance, recurrence, Jira)
pnpm --filter @tt/web dev                                                    # web console on http://localhost:5173
```
Health: `curl localhost:8000/readyz` -> `{"status":"ready",...}`. API docs: <http://localhost:8000/docs>.

## 4. Sign in
Open <http://localhost:5173/login>. Because `TT_DEV_AUTH=1`, a **Development sign-in** button appears: workspace `acme`, handle `@mahmoud`
(admin). Other seeded people: `@sara`, `@youssef`, `@nour`, `@lina` - sign in as them to test what a contributor or a manager sees.
**Never** set `TT_DEV_AUTH` outside a local machine: production refuses to start with it.

Real sign-in methods are configured in `.env` (OIDC, LDAPS/Active Directory, Kerberos behind a proxy): see `docs/security/` and `docs/ops/config-reference.md`.

## 5. The CLI (reporters)
```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/tokens -H 'X-Debug-Actor: @mahmoud' -H 'content-type: application/json' \
        -d '{"workspace":"acme","name":"uat-cli","scopes":["capture"]}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
uv run tt login --url http://localhost:8000 --workspace acme --handle @mahmoud --token "$TOKEN" --time-zone Asia/Riyadh
uv run tt say "fix the login redirect loop, took 2h, plan to finish tomorrow 5pm" --yes
uv run tt now ; uv run tt today
```
Without an LLM gateway the deterministic parser handles the documented syntax (verbs, `#tags`, `@people`, `took 2h`, `plan tomorrow 5pm`,
`est 3d`, `WEB-142`). To try the model-assisted path, run a gateway (`uv run tt gateway serve --local-only` with Ollama, or
`tt-llm serve` with a cloud key; `docs/llm/gateway.md`) and add `--gateway-url http://127.0.0.1:8700` to `tt login`.

## 6. Optional pieces
| Piece | How | Notes |
| --- | --- | --- |
| LLM gateway | `docs/llm/gateway.md` | Needed for: free-text understanding beyond the parser, project insights, review suggestions, "Generate with AI" decks |
| Jira (fake or real) | Setup -> Integrations -> Jira, `docs/integrations/jira.md` | UAT can use the bundled fake Jira in `apps/server/tests/api/fake_jira.py` |
| Slack bot | `docs/client/slack.md` | Needs a Slack workspace |
| Desktop palette | `pnpm --filter @tt/desktop dev` | Browser preview with a stub bridge; the Tauri shell is a scaffold |
| Metrics | set `TT_METRICS_TOKEN`, scrape `/metrics` | Grafana dashboard + alert rules in `ops/` |
| HTTPS | `docs/security/tls.md` | `tt-admin serve --certfile ... --keyfile ...` |

## 7. Run the automated checks (optional but recommended before UAT)
```bash
export TT_TEST_DATABASE_URL=postgresql+psycopg://tt:tt@localhost:5432/tt_test   # an empty database; the tests recreate its schema
make check                       # ruff, mypy, pytest (about 3,000 tests, ~15 min), vitest
make e2e                         # Playwright against a throw-away seeded database (needs Chromium)
```

## 8. Where to go next
* **UAT script**: `docs/uat/uat-script.md` - 14 scenarios with expected results and a sign-off table.
* **Known limitations and open questions**: `docs/uat/known-limitations.md`.
* **Guides**: `docs/guides/reporter.md`, `docs/guides/manager.md`, `docs/guides/admin.md`; runbooks in `docs/runbooks/`.
* **Troubleshooting**: port 5432 refused -> start Postgres (`pg_isready`); `relation ... does not exist` -> run `make migrate`; the web console shows
  "network error" -> the API is not on :8000 (`TT_API_URL=... pnpm --filter @tt/web dev` to point elsewhere); PDF/PNG export 503 -> run
  `make presentation` and `playwright install chromium`; 401 on every call -> sign in again (sessions last 12 h).
