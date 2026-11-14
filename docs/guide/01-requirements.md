# 1. Requirements and support matrix

## Support matrix

| Host OS (x86_64) | Online install (`ttctl setup`) | Air-gapped bundle target | PostgreSQL 16 from | Status |
| --- | --- | --- | --- | --- |
| RHEL / Rocky / Alma **8** | yes | `rhel8-x86_64` | AppStream module `postgresql:16`, or PGDG | supported |
| RHEL / Rocky / Alma **9** | yes | `rhel9-x86_64` | AppStream module `postgresql:16`, or PGDG | supported |
| Ubuntu **22.04** | yes | `ubuntu22.04-amd64` | PGDG apt repository (22.04 ships 14) | supported |
| Ubuntu **24.04** | yes | `ubuntu24.04-amd64` | Ubuntu archive | supported |
| Debian **12** | yes | - (use an internal mirror, [FAQ](13-faq.md)) | PGDG apt repository | supported online |
| macOS (Apple silicon / Intel) | development only | - | Homebrew `postgresql@16` | development |
| Windows | use WSL 2 (Ubuntu) | - | inside WSL | development |
| Any OS with Docker | `ttctl docker up` | `--with-docker-images` | `postgres:16` image | supported |

What each row was actually tested with in CI is listed in the pull request that introduced this matrix and in
`.github/workflows/ops.yml` (jobs `online` and `airgap`, one per target). Clients: see [Clients](08-clients.md).

## What the app needs

| Piece | Version | Needed for | Installed by |
| --- | --- | --- | --- |
| Python | 3.12+ (uv-managed, private; the system python is not touched) | server, worker, CLI, gateway | `ttctl prereqs install python` (user space) |
| uv | 0.4+ (pinned 0.11.32) | Python dependencies, running the apps | `ttctl prereqs install uv` (user space, from PyPI) |
| Node.js | 22 | **only** to build the web console; bundles ship it prebuilt | `ttctl prereqs install node` (user space) |
| pnpm | 9.15.0 | same as Node | `ttctl prereqs install pnpm` (user space) |
| PostgreSQL | **server** 16 + contrib (`pg_trgm`, `ltree`, `btree_gist`) | the database (`--db native`) | `ttctl prereqs install postgresql --system` (root) |
| PostgreSQL client | 16, only for `db backup/restore/psql` | dumps | `ttctl prereqs install psql --system`, or a bundle's `.tools/pgsql` |
| curl | any | downloads, health checks (the bundle's Python is used where curl is missing) | OS package |
| python3 | any (even 3.6) | only to unpack uv the first time (`/usr/libexec/platform-python` on RHEL counts) | OS package (present on standard installs) |
| Chromium + its libraries | any recent | PDF/PNG export of decks | `ttctl prereqs install chromium` + `chromium-libs` |
| git | any, optional | cloning, `prod upgrade` from git | OS package |
| Docker + compose | optional | `--db docker`, `ttctl docker`, building bundles | OS / Docker repository |

`ttctl prereqs check` shows all of this for the host you are on; `-v` adds every probe (path and raw version output).

```bash
scripts/ttctl prereqs check
```
It exits with 3 while a required tool is missing (and prints the install command), 0 when everything required is there.

### What the client psql version means
Setup and `--with-db` check the **server**: `SHOW server_version_num` and `pg_available_extensions` over the database
connection. An older `psql` on the host (RHEL 8 ships 10) does not matter for them. Only `ttctl db backup`, `restore` and
`psql` run a client program, and that must be 16 or newer; they pick `.tools/pgsql`, `/usr/pgsql-16` or the `postgres:16`
container automatically, and say so when none exists (`TT-E109`).

## Root or not
Everything the app needs in user space goes under `.tools/` in the checkout and is put on `PATH` by every script:
uv, Python, Node, pnpm, Chromium. System packages (PostgreSQL, Chromium's libraries, curl, git, Docker) need root.
For those, ttctl **prints the exact commands**, asks before running them (or `--yes`), uses `sudo` only when you are not
root and never silently, and with `--dry-run` only prints.

## Ports

| Port | Used by | Change with |
| --- | --- | --- |
| 5432 | PostgreSQL | `TT_DATABASE_URL` |
| 8000 | API (dev; production behind nginx) | `TT_PORT` |
| 8443 / 8080 | API with its own HTTPS (`prod install --mode tls`) | `tt-admin serve --port/--redirect-port` |
| 5173 | web console | `TT_WEB_PORT` |
| 8700 | LLM gateway | `TT_GATEWAY_PORT` |
| 1420 | desktop preview | fixed |
| 11434 | Ollama (optional) | gateway config `ollama_api_base` |

## Hardware
Laptop / UAT: 2 CPU, 4 GB RAM free, 5 GB disk. Team server: 2 vCPU, 4 GB RAM, 20 GB disk, plus about 8 GB RAM (or a GPU)
if a local 7B model runs on the same host. An air-gapped bundle needs about 2 GB free (unpacked bundle plus the installed release) (sizes in
[Install air-gapped](03-install-airgapped.md#bundle-sizes)).
