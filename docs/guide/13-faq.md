# 13. FAQ

**Do I need root?** Not to install or run the app in user space: uv, Python, Node, pnpm and Chromium go into `.tools/`.
Root is needed for system packages (PostgreSQL, Chromium's libraries) and for production (systemd units, `/etc/tt`).
ttctl shows every root command before running it and asks.

**Does the server need Node?** No. Node only builds the web console. Bundles ship it prebuilt and `ttctl start web` serves
it without Node; production serves it with nginx.

**My host has psql 10 (RHEL 8). Is that a problem?** No for setup, start and migrations: the server's version is what
counts and it is checked over the connection. Yes only for `db backup/restore/psql`, which then use a 16 client from
`.tools/pgsql`, `/usr/pgsql-16` or the `postgres:16` container ([TT-E109](11-troubleshooting.md#tt-e109)).

**Can I use a managed PostgreSQL (RDS, Azure, Cloud SQL)?** Yes: `--db external`, `TT_DATABASE_URL` pointing at it, the
three extensions enabled by the provider's admin, TLS with `TT_DB_SSLMODE=verify-full`.

**What exactly does offline mode block?** With `TT_OFFLINE=1` ttctl refuses downloads (prerequisites, uv/pnpm installs,
Docker pulls/builds, git fetch, bundle create) with exit code 4 and the bundle option that would have provided the piece.
uv, pip, npm, corepack and Playwright are told the same through their own variables. Running the app is not affected.

<a id="internal-mirror"></a>
**We have internal mirrors instead of an air gap. Do I need a bundle?** No. Point the tools at them and use the online
install: `UV_INDEX_URL=https://pypi.mirror.example/simple` (Python packages, uv and pip), `npm_config_registry=https://npm.mirror.example/`
(pnpm), your rpm/apt mirror for PostgreSQL 16 and Chromium's libraries (RHEL: the AppStream module or a PGDG mirror;
Ubuntu 22.04/Debian 12: a PGDG mirror), and `UV_PYTHON_INSTALL_MIRROR` for uv's Python downloads (or install Python 3.12
from your mirror), and `TT_NODE_MIRROR=https://nodejs.mirror.example/dist` for `ttctl prereqs install node`.

**How big is a bundle?** About 260 MB compressed for the base, 550–590 MB with PostgreSQL and Chromium; a local LLM adds
the model size ([sizes](03-install-airgapped.md#bundle-sizes)).

**Can one bundle serve several hosts?** Yes, any number of hosts of the same target. Users' computers use its `client-kit/`.

**How do I see what a command would do before it does it?** `--dry-run` prints every state-changing command instead of
running it; `-v` prints every command and probe as it runs; both write the run log.

**Where are the logs?** `.run/logs/<component>.log` for processes, `.run/logs/ttctl-<command>-<time>.log` for every ttctl
run, journald for production units. `scripts/ttctl support-bundle` collects them with secrets masked.

**Is there a Windows server install?** No; use WSL 2 or Docker for development. Clients run on Windows (client kit).

**Where did the plans, reviews and ADRs go?** To the private planning repository; product docs refer to ADRs by number.
See [Repositories](14-repositories.md).
