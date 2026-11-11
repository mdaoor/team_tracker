#!/usr/bin/env bash
# Upgrade a production install in place: backup -> pull -> install deps/builds -> migrate -> restart.
#
#   sudo scripts/prod/upgrade.sh [GIT_REF]     default: the current branch's upstream
#   options: --no-backup   skip the pre-upgrade database backup (not recommended)
source "$(dirname "$0")/../lib/common.sh"
cd "$ROOT" || exit 1
[[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
BACKUP=1; REF=""
for a in "$@"; do case "$a" in --no-backup) BACKUP=0 ;; -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) REF="$a" ;; esac; done

# shellcheck disable=SC1091
set -a; source /etc/tt/tt.env; set +a
if [[ $BACKUP -eq 1 ]]; then
  title "Backup"
  BACKUP_DIR=/var/backups/tt deploy/backup/backup.sh
fi
title "Update code"
before="$(git rev-parse --short HEAD)"
git fetch --tags origin
if [[ -n "$REF" ]]; then git checkout "$REF"; else git pull --ff-only; fi
ok "$before -> $(git rev-parse --short HEAD)"

title "Dependencies, builds, migrations"
uv sync --all-packages --no-dev --frozen
pnpm install --frozen-lockfile
pnpm --filter @tt/presentation build
pnpm --filter @tt/web build
chmod -R a+rX "$ROOT"
sudo -u tt --preserve-env "$ROOT/.venv/bin/tt-admin" migrate

title "Restart"
"$ROOT/scripts/prod/service.sh" restart
sleep 2
"$ROOT/scripts/prod/service.sh" status
echo "Roll back: sudo scripts/prod/upgrade.sh $before   (migrations are additive; restore the backup only if data must go back)"
