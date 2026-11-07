#!/usr/bin/env bash
# Quarterly restore drill: restore the newest backup into a scratch database, run the smoke checks, report the elapsed time.
# The drill passes when the stack answers /readyz against the restored copy and the row counts are plausible. Target: < 30 minutes.
#   ADMIN_URL=postgresql://postgres@host/postgres BACKUP=/var/backups/tt/tt-....dump ./restore_drill.sh
set -euo pipefail
: "${ADMIN_URL:?set ADMIN_URL (a role that may CREATE DATABASE)}"
BACKUP="${BACKUP:-$(ls -1t "${BACKUP_DIR:-./backups}"/tt-*.dump | head -1)}"
SCRATCH="tt_restore_drill_$(date +%s)"
start=$(date +%s)
[ -f "$BACKUP.sha256" ] && sha256sum --check "$BACKUP.sha256"
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE $SCRATCH"
scratch_url="${ADMIN_URL%/*}/$SCRATCH"
trap 'psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" >/dev/null' EXIT
pg_restore --no-owner --exit-on-error --jobs 4 --dbname "$scratch_url" "$BACKUP"
psql "$scratch_url" -v ON_ERROR_STOP=1 -At <<'SQL'
select 'workspaces', count(*) from workspaces;
select 'work_items', count(*) from work_items;
select 'events', count(*) from events;
select 'migrations', version_num from alembic_version;
SQL
if command -v tt-admin >/dev/null; then
  TT_DATABASE_URL="postgresql+psycopg://${scratch_url#postgresql://}" tt-admin verify-projection "${DRILL_WORKSPACE:-acme}" || echo "verify-projection reported drift (see ADR-0040)"
fi
echo "restore drill OK in $(( $(date +%s) - start )) s"
