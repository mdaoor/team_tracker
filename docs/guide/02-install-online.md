# 2. Install on a connected host

Three ways. For a host without internet see [Install air-gapped](03-install-airgapped.md).

| Way | For | Section |
| --- | --- | --- |
| Local (no Docker) | development, UAT, a single small server | [A](#a-local) |
| Docker compose | trying it without installing PostgreSQL/Python/Node | [B](#b-docker) |
| Production | systemd + nginx on a server | [Production](06-production.md) |

## A. Local

### 1. Get the code
```bash
git clone https://github.com/mdaoor/team_tracker && cd team_tracker
```

### 2. Install everything, including the database
```bash runnable
scripts/ttctl setup --with-db --install-missing --browsers --yes
```
What it does, step by step (each step is printed; `-v` streams the output, the full log is in `.run/logs/`):

1. **Prerequisites.** Checks uv, Python, curl, Node, pnpm. Missing user-space tools are installed into `.tools/` (no root):
   with `--install-missing` or `--yes` at once, otherwise it asks ("pnpm 9.15.0 is missing. Install it into .tools/ now? [Y/n]").
   Without a terminal and without those flags it stops with exit code 3 and prints the exact command.
2. **Configuration.** Writes `.env` from `.env.example` if missing (dev defaults, Development sign-in on). Never overwrites.
3. **Python.** `uv sync` creates `.venv/`.
4. **Web.** `pnpm install` and builds the deck viewer.
5. **Database** (`--with-db`). Installs PostgreSQL 16 if it is missing (root; shows the commands and asks), starts it,
   creates the role/database/extensions from `TT_DATABASE_URL`, checks the server version and extensions, migrates and
   loads the demo workspace **acme**.

Example of what you see on a RHEL 8 host without pnpm:
```text
1/5 Prerequisites
 ok uv 0.13.8
 ok python 3.13.18
 ok curl 7.61.1
 ok node 22.11.0
warn pnpm is missing (need 9.15.0)
note psql 10.23 found. That is fine for setup and --with-db: the version that matters is the SERVER's, checked below with
     SHOW server_version_num. Only 'scripts/ttctl db backup|restore|psql' need a 16 client; ...
[1] install pnpm into .tools
...
```

Afterwards every required tool is present (exit code 0):
```bash runnable
scripts/ttctl prereqs check
```

Database choices: `--db native` (default, PostgreSQL on this host), `--db docker` (the compose `db` container),
`--db external` (a server you run; point `TT_DATABASE_URL` at it first). See [Database](05-database.md).

### 3. Start
```bash runnable
scripts/ttctl start
```
```bash runnable
scripts/ttctl status
```
Open <http://localhost:5173/login> → **Development sign-in** → workspace `acme`, handle `@mahmoud`. API docs: <http://localhost:8000/docs>.

### 4. Check it end to end
```bash runnable
.venv/bin/python ci/ops/smoke.py
```
Checks `/healthz`, `/readyz`, the demo data, the web console and a PDF export through Chromium (installed by
`--browsers` above, together with its system libraries).

### 5. Stop
```bash runnable
scripts/ttctl stop
```

## B. Docker
```bash
scripts/ttctl docker up
```
```bash
scripts/ttctl docker seed
```
Postgres, API (:8000), worker and web (:5173) in containers, source mounted for live reload; for development and demos.
`--llm` adds the LLM gateway and Ollama, `--tls` the API with its own HTTPS. Stop with `scripts/ttctl docker down`;
`scripts/ttctl docker reset` also deletes the database volume.

## Behind a proxy or with an internal mirror
Set `HTTPS_PROXY` as usual. Internal mirrors: `UV_INDEX_URL` (Python packages), `npm_config_registry` (npm/pnpm), and
your rpm/apt mirror for the system packages. See the [FAQ](13-faq.md#internal-mirror).
