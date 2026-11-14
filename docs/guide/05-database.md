# 5. Database

PostgreSQL **16** with the contrib extensions `pg_trgm`, `ltree` and `btree_gist`. The connection is `TT_DATABASE_URL`
in `.env` (keep the `postgresql+psycopg://` prefix).

## Three modes
| `--db` / `TT_DB_MODE` | Server | `db start/stop` | `db create` |
| --- | --- | --- | --- |
| `native` (default) | PostgreSQL 16 on this host (`ttctl prereqs install postgresql --system`, or a bundle with `--with-db`) | the OS service (systemd, pg_ctlcluster, pg_ctl, Homebrew) | as the `postgres` superuser |
| `docker` | the `db` service of `docker-compose.yml` | the container | the container creates role and database |
| `external` | a server you run (managed service, DBA team) | nothing | with `TT_DB_ADMIN_URL`, otherwise only checks |

`ttctl setup --with-db --db external` saves the mode in `.env`.

## Everyday commands
```bash runnable
scripts/ttctl db status
```
```text
 ok connected to 'tt' on localhost:5432 as 'tt' - server PostgreSQL 16.15
 ok extensions pg_trgm ltree btree_gist available
 ok migration revision 0014
```
`status` asks the **server** (`SHOW server_version_num`, `pg_available_extensions`), so an old local `psql` does not
matter. `db check` does the same and fails with a precise code: unreachable (`TT-E200`), login refused (`TT-E201`),
server older than 16 (`TT-E202`), extensions missing (`TT-E203`, names the contrib package to install).

| Action | Command |
| --- | --- |
| start / stop | `scripts/ttctl db start` · `db stop` |
| everything for a new install | `scripts/ttctl db setup` (start, create, check, migrate, seed) |
| create role + database + extensions | `scripts/ttctl db create` |
| migrate after an upgrade | `scripts/ttctl db migrate` |
| demo workspace "acme" | `scripts/ttctl db seed` (`--reset` recreates it) |
| wipe and recreate with demo data | `scripts/ttctl db reset` (asks; `--yes`) |
| drop | `scripts/ttctl db drop` (asks; refuses while server/worker run) |
| SQL shell | `scripts/ttctl db psql` |
| event log vs tables | `scripts/ttctl db verify acme` |

## Backup and restore
```bash runnable
scripts/ttctl db backup .run/backup-test.dump
```
```bash
scripts/ttctl db restore .run/backup-test.dump --yes
```
`pg_dump -Fc` / `pg_restore --no-owner --no-comments`. These need a 16 client: `.tools/pgsql` (bundles), `/usr/pgsql-16`,
`/usr/lib/postgresql/16` or the `postgres:16` container, else `TT-E109` with the install command. Nightly backups and the
restore drill for production: [Production](06-production.md#backups).

## Create by hand
For a DBA, or when `db create` cannot get superuser rights:
```sql
CREATE ROLE tt LOGIN CREATEDB PASSWORD 'choose-one';
CREATE DATABASE tt OWNER tt;
\c tt
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS btree_gist;
```
Then set `TT_DATABASE_URL=postgresql+psycopg://tt:choose-one@HOST:5432/tt` and run `scripts/ttctl db check`.
For TLS to the database: `TT_DB_SSLMODE=verify-full` and `TT_DB_SSLROOTCERT` ([Configuration](09-configuration.md)).
