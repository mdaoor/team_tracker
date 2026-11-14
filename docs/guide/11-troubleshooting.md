# 11. Troubleshooting

Every error from ttctl has the same shape and a stable code:
```text
error TT-E104  pnpm is missing
  WHAT  pnpm missing
  WHY   pnpm installs and builds the web console; no terminal to ask and neither --install-missing nor --yes was given
  FIX   scripts/ttctl setup --install-missing --with-db
  DOCS  docs/guide/11-troubleshooting.md#tt-e104
  LOG   .run/logs/ttctl-setup-20261114-091249-7.log
```
First steps for anything: re-run with `-v` (every command and probe) and read the `LOG` file; `scripts/ttctl doctor`;
`scripts/ttctl support-bundle` to send everything (masked) to support. Exit codes: 1 runtime, 2 usage, 3 prerequisite
missing, 4 blocked because offline, 5 configuration.

## TT-E001
**A command failed unexpectedly** (exit 1). Something not covered by a specific check failed; the report names the exact
command, its exit code and `script:line`. Fix: re-run with `-v` (or `-vv` to trace), read the log; if it looks like a
bug in the scripts, attach `scripts/ttctl support-bundle` to the issue.

## TT-E002
**A step failed** (exit 1). A numbered step (uv sync, pnpm install, migrate, ...) exited non-zero; the last 40 lines of
its output are printed above the report and the full output is in the log. Fix the cause shown there; `-v` streams the
output live. Common causes: no network or proxy (`HTTPS_PROXY`), disk full, a database that is not running.

## TT-E010
**Unknown option or argument** (exit 2). Check the synopsis: `scripts/ttctl help COMMAND`. Global options (`-v`, `--dry-run`,
`--yes`, ...) are accepted anywhere; command options are listed in [Scripts reference](10-scripts-reference.md).

## TT-E011
**Unknown command or subcommand** (exit 2). `scripts/ttctl help` lists the commands. Older names keep working
(`up`, `down`, `ps`, `log`, `tt`, `test`).

## TT-E012
**Missing or invalid argument** (exit 2), e.g. `db restore` without a file, `repo` without a subcommand, a dump file that
does not exist, a rollback with no previous release. The report says which; the FIX line shows a complete command.

## TT-E013
**Confirmation required** (exit 2). A destructive or root action asks first. Without a terminal (CI, ssh with a pipe)
it cannot ask: read what it will do with `--dry-run`, then repeat with `--yes`.

## TT-E020
**Root privileges required** (exit 3). `prod install/upgrade/rollback` and installing system packages need root. Run with
`sudo`, or give the printed commands to an administrator. ttctl never calls sudo without showing the command first.

## TT-E030
**Project dependencies are not installed** (exit 3). `.venv` (Python) or the web console (built `apps/web/dist`, or Node
with `node_modules`) is missing. Fix: `scripts/ttctl setup`. Offline: install the bundle (`./install.sh`), which brings
`.venv` and the prebuilt web console.

## TT-E100
**A required tool is missing or too old** (exit 3). The report names the tool and the version found. User-space tools
(uv, Python, Node, pnpm) install without root: `scripts/ttctl prereqs install TOOL` or `scripts/ttctl setup --install-missing`.
System tools (curl, git) need the OS package manager; `scripts/ttctl prereqs check` shows everything at once.
Offline: `scripts/ttctl prereqs install TOOL --from-bundle DIR`.

## TT-E104
**pnpm is missing** (exit 3). Needed only to build the web console. `scripts/ttctl prereqs install pnpm` puts pnpm 9.15.0
into `.tools/` (needs Node, installed the same way, and the npm registry). Bundle installs ship the web console prebuilt
and do not need pnpm.

## TT-E107
**Chromium is missing** (exit 3). Only PDF/PNG export of decks needs it. `scripts/ttctl setup --browsers` (or
`prereqs install chromium`); its system libraries: `scripts/ttctl prereqs install chromium-libs --system`. Offline: a
bundle built with `--with-browsers`.

## TT-E108
**Docker is missing or not running** (exit 3). Needed only for `ttctl docker`, `--db docker` and building bundles.
Start it (`sudo systemctl start docker`, or Docker Desktop) or use the local install / `--db native`.

## TT-E109
**The PostgreSQL client is too old for this action** (exit 3). `db backup`, `restore` and `psql` need a client of version 16+
(an older client cannot handle a 16 dump reliably). Everything else works with any client. Fix:
`scripts/ttctl prereqs install psql --system` (installs the 16 client next to the old one; RHEL: `postgresql16` from
PGDG or the `postgresql:16` module), or have Docker available (the `postgres:16` image is used), or a bundle with
`--with-db` (ships the client in `.tools/pgsql`).

## TT-E110
**This operating system is not supported by the installer** (exit 3). `prereqs install` knows RHEL/Rocky/Alma 8 and 9,
Ubuntu 22.04 and 24.04, Debian 12 and macOS. On other systems install the [requirements](01-requirements.md) with your
own tools; every other ttctl command works.

## TT-E120
**Installing a prerequisite failed** (exit 1). The package manager or download returned an error (shown above). Typical:
repository not reachable (proxy, mirror), a bundle built for another OS release, a checksum mismatch of a download.
Re-run with `-v`; run the printed command by hand to see the package manager's own message.

## TT-E200
**The database server is not reachable** (exit 1). Nothing answers at the host/port of `TT_DATABASE_URL`.
`native`: `scripts/ttctl db start` (not installed: `scripts/ttctl prereqs install postgresql --system`); `docker`:
`scripts/ttctl db start --db docker`; `external`: check the host, port, firewall and that the server runs.

## TT-E201
**The database rejected the login** (exit 5). User or password in `TT_DATABASE_URL` is wrong, or the role does not exist.
Create it: `scripts/ttctl db create`, or correct `.env`. In production check `/etc/tt/tt.env` and `pg_hba.conf`
(scram-sha-256 for host connections).

## TT-E202
**The database server is older than PostgreSQL 16** (exit 3). The check reads the server's own version
(`SHOW server_version_num`). Install PostgreSQL 16 (`scripts/ttctl prereqs install postgresql --system`; RHEL 8 offers
the `postgresql:16` module stream or PGDG) or point `TT_DATABASE_URL` at a 16 server. Offline: `./install.sh --with-db`
from a bundle built with `--with-db rpm|deb`.

## TT-E203
**Required PostgreSQL extensions are not available** (exit 3). `pg_trgm`, `ltree` and `btree_gist` come with the contrib
package: RHEL module stream `postgresql-contrib`, RHEL PGDG `postgresql16-contrib`, Ubuntu/Debian part of
`postgresql-16`. Install it on the **database host**, then `scripts/ttctl db create`.

## TT-E204
**The database schema is not migrated** (exit 1). `scripts/ttctl db migrate` (new install: `scripts/ttctl db setup`).

## TT-E205
**The role or database could not be created** (exit 5). `db create` needs a superuser: locally the `postgres` OS user
(sudo/root), elsewhere `TT_DB_ADMIN_URL=postgresql://postgres:PASSWORD@HOST:5432/postgres`. Or let a DBA run the SQL in
[Database](05-database.md#create-by-hand).

## TT-E206
**The database service cannot be started here** (exit 3). PostgreSQL 16 is not installed, has no data directory, or the
host has no known service manager. Install it (`prereqs install postgresql --system`), initialise the data directory
(`postgresql-16-setup initdb` on RHEL PGDG, `postgresql-setup --initdb` with the module), or use `--db docker` / `--db external`.

## TT-E207
**The application is using the database** (exit 1). `db drop`, `reset` and `restore` refuse while server or worker run:
`scripts/ttctl stop` first.

## TT-E300
**A port is already in use** (exit 1). Another program listens on the port (`status` shows `external`). Find it with
`ss -ltnp | grep :PORT`; stop it, or change `TT_PORT` / `TT_WEB_PORT` / `TT_GATEWAY_PORT` in `.env`.

## TT-E301
**A component did not become healthy** (exit 1). The last 25 log lines are printed. Common: database down, wrong
`TT_DATABASE_URL`, unsafe production settings (`TT_SESSION_SECRET` / `TT_SECRET_KEY` / `TT_DEV_AUTH`). After an upgrade
the previous release is already running again; read `scripts/ttctl prod logs tt-server`.

## TT-E303
**A component is still running** (exit 1). `logs --clear` and `clean` refuse while components run: `scripts/ttctl stop`.

## TT-E304
**A required setting is missing** (exit 5), e.g. the Slack tokens for `start slack`. Add the named variable to `.env`.

## TT-E305
**Development sign-in is required** (exit 5). `client token` / `client login` mint tokens through the Development sign-in,
which only exists with `TT_DEV_AUTH=1` (never in production). On a real server create a token in the web console
(Profile → API tokens) and use `tt login --token`.

## TT-E306
**The API is not reachable** (exit 1). Start it (`scripts/ttctl start server`, production `scripts/ttctl prod start`) and
check `scripts/ttctl status`.

## TT-E310
**No log file yet** (exit 1). The component has not been started with ttctl on this install. `scripts/ttctl logs --runs`
lists the run logs of ttctl commands.

## TT-E400
**Network access refused in offline mode** (exit 4). `TT_OFFLINE=1` (set by bundle installs in `.ttctl.env`) makes every
download stop before it starts. Use the bundle's copy of what you need; on a connected host unset `TT_OFFLINE`.

## TT-E401
**The bundle does not contain this component** (exit 4). The report names the component and the `bundle create` option
that adds it (`--with-db`, `--with-browsers`, `--with-dev`, `--with-llm`, `--with-docker-images`). Build a new bundle on
the connected machine with that option and install it (`./install.sh`, or `prod upgrade --from-bundle` in production).

## TT-E402
**The bundle failed verification** (exit 1). A file does not match `SHA256SUMS` or the signature does not verify: the
copy is damaged or was changed. Copy it again, compare with the `.sha256` file, check with `scripts/ttctl bundle verify FILE`.
For signed bundles import the publisher's key (`gpg --import`).

## TT-E403
**The bundle was built for another platform** (exit 5). Binaries are built for the target's C library and packages. Build
one for this host (`--target` matching `scripts/ttctl prereqs check`), or `--force` to try anyway.

## TT-E404
**Building the bundle failed** (exit 1). Read the step output; the build runs in a container of the target OS and needs
Docker plus access to PyPI, nodejs.org, the npm registry and the OS repositories (or `TT_CI_EXTRA_CA` behind an
intercepting proxy). `--browsers-from DIR` avoids the Chromium download.

## TT-E500
**Unsafe or invalid configuration** (exit 5): production settings missing (`/etc/tt/tt.env`), `--from-bundle` on a git
install, `TT_DEV_AUTH` on outside dev. The report names the setting; see [Configuration](09-configuration.md).

## TT-E502
**systemd is required** (exit 3) for `prod` commands. On hosts without systemd use `scripts/ttctl start/stop/status`, Docker
images or Helm ([Production](06-production.md#containers-without-kubernetes)).

## TT-E503
**TLS certificate files are missing** (exit 5). `prod install --mode tls` needs `/etc/tt/tls/cert.pem` (full chain) and
`key.pem` (mode 600, owner tt). See [TLS](06-production.md#tls), or use `--mode proxy` behind nginx.

## TT-E600
**Repository operation refused** (exit 1). `repo split` will not push to a repository that is not private, stops when the
secrets scan finds something (review the report; re-run with `--no-scan` only for known test fixtures), or lacks rights to
create the repository (the owner runs the printed command). `repo purge-history` refuses to rewrite without its explicit flag.

## Other symptoms
| Symptom | Fix |
| --- | --- |
| web console shows "network error" | the API is not where the web console forwards (`TT_API_URL`); `scripts/ttctl start server` |
| no "Development sign-in" button | `TT_DEV_AUTH=1` in `.env` (dev only), `scripts/ttctl restart server` |
| 401 everywhere | session expired (12 h) or token revoked: sign in again |
| PDF/PNG export answers 503 | Chromium or the deck viewer missing: `scripts/ttctl setup --browsers`, `scripts/ttctl build presentation` |
| `tt: command not found` | `uv tool update-shell`, open a new terminal |
| `tar: ... zstd: Cannot exec` | install `zstd`, or use a bundle built with `--compress gz` |
| stale PID after a reboot | harmless; the scripts check the process is alive |
