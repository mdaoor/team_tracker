#!/usr/bin/env bash
# Logical backup of the Team Tracker database (custom format, restorable with pg_restore, selective and parallel).
# For point-in-time recovery use WAL archiving (pgBackRest / managed snapshots) in addition; this script is the portable baseline.
#   TT_DATABASE_URL=postgresql://user:pw@host/tt BACKUP_DIR=/var/backups/tt ./backup.sh
set -euo pipefail
: "${TT_DATABASE_URL:?set TT_DATABASE_URL}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
url="${TT_DATABASE_URL/postgresql+psycopg:/postgresql:}"
out="$BACKUP_DIR/tt-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --file "$out" "$url"
sha256sum "$out" > "$out.sha256"
find "$BACKUP_DIR" -name 'tt-*.dump*' -mtime +"$KEEP_DAYS" -delete
echo "$out"
