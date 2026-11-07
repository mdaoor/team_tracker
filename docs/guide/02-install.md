# 2. Install

There are three ways. Pick one:

| Way | Best for | Section |
| --- | --- | --- |
| **Local** (no Docker) | development, UAT on a laptop, fastest feedback | [A](#a-local-install-no-docker) |
| **Docker compose** | trying it out without installing Postgres/Python/Node | [B](#b-docker-install) |
| **Production server** | a real team install | [Production](04-production.md) |

---

## A. Local install (no Docker)

### Step 1 - get the code
```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```

### Step 2 - install everything and create the database
```bash
scripts/ttctl setup --with-db
```
This runs, in order:

1. **Prerequisites check** (`scripts/doctor.sh --tools-only`): stops with a clear message if a tool is missing.
2. **`.env`** is created from `.env.example` if it does not exist (dev defaults: database `postgresql+psycopg://tt:tt@localhost:5432/tt`,
   Development sign-in on). An existing `.env` is never overwritten.
3. **Python dependencies**: `uv sync --all-packages` (creates `.venv/`).
4. **Node dependencies**: `pnpm install --frozen-lockfile`, then builds the single-file **deck viewer**
   (`apps/presentation/dist/presentation.html`) used by Present / PDF / PNG.
5. **Database** (`--with-db`): starts the local Postgres service, creates the role and database from `TT_DATABASE_URL`
   and the three extensions, runs migrations (`tt-admin migrate`) and seeds the demo workspace **acme**
   (5 projects, 49 items, a plan-only project "ERP", people, policies).

Options: `--no-seed` (empty database), `--browsers` (download Chromium for PDF/PNG export and browser tests).
Running `setup` again is safe; every step skips what is already done.

> Creating the role and database needs Postgres superuser rights. The script uses `sudo -u postgres psql` (Linux) or your
> own user (Homebrew). For a remote or managed database set `TT_DB_ADMIN_URL=postgresql://postgres:PASSWORD@host:5432/postgres`
> before running it, or create the database yourself:
> ```sql
> CREATE ROLE tt LOGIN CREATEDB PASSWORD 'tt';
> CREATE DATABASE tt OWNER tt;
> \c tt
> CREATE EXTENSION pg_trgm; CREATE EXTENSION ltree; CREATE EXTENSION btree_gist;
> ```

### Step 3 - start it
```bash
scripts/ttctl start
```
Starts **server**, **worker** and **web** in the background and waits until each is healthy.

### Step 4 - sign in
Open <http://localhost:5173/login> and click **Development sign-in**: workspace `acme`, handle `@mahmoud` (admin).
Other demo people: `@sara`, `@youssef`, `@nour`, `@lina` (sign in as them to see a contributor's or a manager's view).
API documentation: <http://localhost:8000/docs>.

### Step 5 - try the command-line client
```bash
scripts/ttctl client login
```
```bash
scripts/ttctl client run say "fix the login redirect loop, took 2h, plan to finish tomorrow 5pm" --yes
```
```bash
scripts/ttctl client run today
```
See [Clients](05-clients.md) to install `tt` as a normal command.

### Step 6 - verify (optional)
```bash
scripts/ttctl doctor
```

### Doing the same by hand
Every script step maps to a plain command, if you prefer to type them:

| Step | Command |
| --- | --- |
| config | `cp .env.example .env` |
| Python deps | `uv sync --all-packages` |
| Node deps | `pnpm install --frozen-lockfile` |
| deck viewer | `pnpm --filter @tt/presentation build` |
| database | `sudo -u postgres psql -c "CREATE USER tt WITH PASSWORD 'tt' CREATEDB" -c "CREATE DATABASE tt OWNER tt"` |
| migrate | `uv run tt-admin migrate` |
| seed | `uv run tt-admin seed demo` |
| API | `uv run uvicorn tt_server.main:create_app --factory --reload --port 8000` |
| worker | `uv run python -m tt_worker` |
| web | `pnpm --filter @tt/web dev` |

---

## B. Docker install

Needs Docker with the compose plugin; nothing else.

```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```
```bash
scripts/ttctl docker up
```
This builds the server image and starts **db** (Postgres 16), **server** (:8000, runs migrations on start), **worker** and
**web** (:5173). The first start of `web` runs `pnpm install` inside the container and takes a few minutes; the script waits for it.

Load the demo data once:
```bash
scripts/ttctl docker seed
```
Open <http://localhost:5173/login> (Development sign-in as above).

Extras:

| Want | Command |
| --- | --- |
| LLM gateway + Ollama containers | `scripts/ttctl docker up --llm` |
| API with its own HTTPS on :8443 (PEM files in `deploy/tls/`) | `scripts/ttctl docker up --tls` |
| status / logs | `scripts/ttctl docker ps`, `scripts/ttctl docker logs -f server` |
| stop, keep data | `scripts/ttctl docker down` |
| delete everything including the database volume | `scripts/ttctl docker reset` |

The compose file mounts the source tree, so code changes reload live. It is meant for development and demos, not production
(see [Production](04-production.md)).

---

## Uninstall

| What | Command |
| --- | --- |
| stop everything | `scripts/ttctl stop --with-db` (local) or `scripts/ttctl docker down` |
| remove the database | `scripts/ttctl db drop` or `scripts/ttctl docker reset` |
| remove dependencies and build output | `scripts/ttctl clean --deps` |
| remove the `tt` CLI | `scripts/ttctl client uninstall` |
| remove client data | delete `~/.config/tt` (sign-in) and `~/.local/share/tt` (outbox, personal store) |
| remove everything | delete the repository folder |
