# 1. Requirements

## What you need

| Tool | Version | Needed for | Check |
| --- | --- | --- | --- |
| Python | 3.12 or newer | server, worker, CLI, gateway, Slack bot | `python3 --version` |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | Python dependencies and running the Python apps | `uv --version` |
| Node.js | 22 | web console, deck viewer, desktop palette | `node --version` |
| pnpm | 9 (the repo pins 9.15.0) | Node dependencies | `pnpm --version` |
| PostgreSQL | 16, with `pg_trgm`, `ltree`, `btree_gist` (they ship with Postgres) | the database | `psql --version` |
| curl, git | any | scripts, cloning | |
| Chromium | any recent, optional | PDF/PNG export of decks, browser tests | installed by `scripts/ttctl setup --browsers` |
| Docker + compose plugin | optional | the Docker way of running everything | `docker compose version` |
| Ollama | optional | a local model for the LLM gateway | `ollama --version` |
| Rust + Tauri libraries | optional | building the native desktop app only | `cargo --version` |

`scripts/ttctl doctor` checks all of this for you and tells you what is missing.

## Install the tools

### Ubuntu / Debian
```bash
sudo apt update && sudo apt install -y curl git postgresql-16 postgresql-client-16
```
On older releases where `postgresql-16` is not in the default list, add the PostgreSQL apt repository first
(<https://www.postgresql.org/download/linux/ubuntu/>).
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```
```bash
sudo corepack enable && corepack prepare pnpm@9.15.0 --activate
```

### Fedora / RHEL
```bash
sudo dnf install -y curl git postgresql-server postgresql-contrib nodejs   # check: postgres --version shows 16, node --version shows 22
```
```bash
sudo postgresql-setup --initdb && sudo systemctl enable --now postgresql
```
Then install uv and pnpm as above.

### macOS (Homebrew)
```bash
brew install uv node@22 postgresql@16 git
```
```bash
brew services start postgresql@16 && corepack enable
```

### Windows
Use **WSL 2** with Ubuntu and follow the Ubuntu steps inside WSL. The scripts are bash scripts; they do not run in PowerShell.
Docker Desktop with the WSL 2 backend works for the Docker path.

## Ports used

| Port | Used by | Change with |
| --- | --- | --- |
| 5432 | PostgreSQL | `TT_DATABASE_URL` |
| 8000 | API server (dev, and production behind nginx) | `TT_PORT` |
| 8443 / 8080 | API server with its own HTTPS (production `--mode tls`) | `tt-admin serve --port/--redirect-port` |
| 5173 | web console dev server | `TT_WEB_PORT` |
| 8700 | LLM gateway | `TT_GATEWAY_PORT` |
| 1420 | desktop palette preview (fixed by its Vite config) | - |
| 11434 | Ollama (if you use it) | `ollama_api_base` in the gateway config |

## Hardware
A laptop is enough for development and UAT (2 CPU, 4 GB RAM free). A small team server: 2 vCPU, 4 GB RAM, 20 GB disk,
plus whatever a local model needs if you run Ollama on the same box (7B models want about 8 GB RAM or a GPU).
