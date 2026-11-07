#!/usr/bin/env bash
# Database control (PostgreSQL 16). Connection details come from TT_DATABASE_URL in .env.
#
#   scripts/db.sh start            start the local Postgres service (systemd / service / pg_ctlcluster / Homebrew),
#                                  or the "db" container when TT_DB_MODE=docker
#   scripts/db.sh stop             stop it
#   scripts/db.sh status           is it up, can we connect, which migration revision
#   scripts/db.sh create           create the role and database from TT_DATABASE_URL (+ pg_trgm, ltree, btree_gist)
#   scripts/db.sh migrate          tt-admin migrate (alembic upgrade head + job-queue schema)
#   scripts/db.sh seed [--reset]   demo workspace "acme" (--reset truncates everything first)
#   scripts/db.sh setup            start + create + migrate + seed
#   scripts/db.sh reset            drop and re-create the database, migrate, seed   (asks first; --yes to skip)
#   scripts/db.sh drop             drop the database                                 (asks first; --yes to skip)
#   scripts/db.sh backup [FILE]    pg_dump -Fc to FILE (default backups/tt-YYYYmmdd-HHMMSS.dump)
#   scripts/db.sh restore FILE     restore a dump into the (emptied) database        (asks first; --yes to skip)
#   scripts/db.sh psql [ARGS]      open psql on the app database
#   scripts/db.sh verify [WS]      tt-admin verify-projection (event log vs tables) for workspace WS (default acme)
#
# TT_DB_MODE=docker uses the "db" service of docker-compose.yml instead of a local Postgres.
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1

MODE="${TT_DB_MODE:-local}"
USER_="$(db_part user)"; PASS="$(db_part password)"; HOST="$(db_part host)"; PORT="$(db_part port)"; NAME="$(db_part name)"
YES=0; ARGS=()
for a in "$@"; do [[ "$a" == "--yes" || "$a" == "-y" ]] && YES=1 || ARGS+=("$a"); done
set -- "${ARGS[@]+"${ARGS[@]}"}"
CMD="${1:-status}"; shift || true

confirm() { [[ $YES -eq 1 ]] && return 0; read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[Yy]$ ]]; }

# Run SQL as a Postgres superuser: "sudo -u postgres psql" locally, the container's psql in docker mode,
# or TT_DB_ADMIN_URL (e.g. postgresql://postgres:secret@dbhost:5432/postgres) for a remote server.
admin_psql() {
  if [[ -n "${TT_DB_ADMIN_URL:-}" ]]; then psql "$TT_DB_ADMIN_URL" -v ON_ERROR_STOP=1 "$@"
  elif [[ "$MODE" == "docker" ]]; then docker compose exec -T db psql -U "$USER_" -d postgres -v ON_ERROR_STOP=1 "$@"
  elif [[ "$(id -u)" -eq 0 ]] && id postgres >/dev/null 2>&1; then (cd / && su postgres -c "psql -v ON_ERROR_STOP=1 $(printf '%q ' "$@")")
  elif have sudo && id postgres >/dev/null 2>&1; then (cd / && sudo -u postgres psql -v ON_ERROR_STOP=1 "$@")
  else psql -d postgres -v ON_ERROR_STOP=1 "$@"   # Homebrew: the current user is the superuser
  fi
}
db_up() { if have pg_isready; then pg_isready -q -h "$HOST" -p "$PORT"; else port_in_use "$PORT"; fi; }

start_db() {
  if db_up; then ok "Postgres already up on $HOST:$PORT"; return; fi
  if [[ "$MODE" == "docker" ]]; then
    docker compose up -d db
  elif have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^postgresql'; then
    sudo_ systemctl start postgresql
  elif have pg_ctlcluster && ls /etc/postgresql/*/main >/dev/null 2>&1; then
    local v; v="$(ls /etc/postgresql | sort -V | tail -1)"; sudo_ pg_ctlcluster "$v" main start
  elif have service && [[ -x /etc/init.d/postgresql ]]; then
    sudo_ service postgresql start
  elif have brew; then
    brew services start postgresql@16
  else
    die "do not know how to start Postgres here - start it yourself, or set TT_DB_MODE=docker"
  fi
  for _ in $(seq 1 40); do db_up && { ok "Postgres up on $HOST:$PORT"; return; }; sleep 0.5; done
  die "Postgres did not come up on $HOST:$PORT"
}
stop_db() {
  if [[ "$MODE" == "docker" ]]; then docker compose stop db; return; fi
  if have systemctl && systemctl list-unit-files 2>/dev/null | grep -q '^postgresql'; then sudo_ systemctl stop postgresql
  elif have pg_ctlcluster && ls /etc/postgresql/*/main >/dev/null 2>&1; then sudo_ pg_ctlcluster "$(ls /etc/postgresql | sort -V | tail -1)" main stop
  elif have service && [[ -x /etc/init.d/postgresql ]]; then sudo_ service postgresql stop
  elif have brew; then brew services stop postgresql@16
  else die "do not know how to stop Postgres here"; fi
  ok "Postgres stopped"
}
sudo_() { if [[ "$(id -u)" -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

create_db() {
  [[ "$MODE" == "docker" ]] && { ok "the db container creates role '$USER_' and database '$NAME' itself"; return; }
  info "creating role '$USER_' and database '$NAME' (if missing)"
  admin_psql -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$USER_') THEN
    CREATE ROLE "$USER_" LOGIN CREATEDB PASSWORD '${PASS//\'/\'\'}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE "$NAME" OWNER "$USER_"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$NAME')\gexec
SQL
  # the migrations create these, but only a superuser may; doing it here avoids "permission denied to create extension"
  admin_psql -q -d "$NAME" -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm' -c 'CREATE EXTENSION IF NOT EXISTS ltree' \
    -c 'CREATE EXTENSION IF NOT EXISTS btree_gist'
  ok "database '$NAME' ready"
}
drop_db() {
  confirm "Drop database '$NAME' on $HOST:$PORT? All data is lost." || die "cancelled"
  for c in server worker; do running_pid "$c" >/dev/null && die "stop the app first: scripts/ttctl stop"; done
  admin_psql -q -c "DROP DATABASE IF EXISTS \"$NAME\" WITH (FORCE)"
  ok "database '$NAME' dropped"
}
status_db() {
  if db_up; then ok "Postgres up on $HOST:$PORT (mode: $MODE)"; else warn "Postgres down on $HOST:$PORT (mode: $MODE)"; return 1; fi
  if have psql && psql "$(db_libpq_url)" -tAc 'select 1' >/dev/null 2>&1; then
    ok "connected to '$NAME' as '$USER_'"
    local rev; rev="$(psql "$(db_libpq_url)" -tAc 'select version_num from alembic_version' 2>/dev/null || true)"
    [[ -n "$rev" ]] && ok "migration revision $rev" || warn "not migrated (scripts/ttctl db migrate)"
  else
    warn "cannot connect to '$NAME' as '$USER_' (scripts/ttctl db create)"
  fi
}

case "$CMD" in
  start) start_db ;;
  stop) stop_db ;;
  status) status_db ;;
  create) create_db ;;
  migrate) info "migrating"; "${PY_RUN[@]}" tt-admin migrate ;;
  seed) info "seeding demo workspace 'acme'"; "${PY_RUN[@]}" tt-admin seed demo "$@" ;;
  setup) start_db; create_db; "${PY_RUN[@]}" tt-admin migrate; "${PY_RUN[@]}" tt-admin seed demo ;;
  drop) drop_db ;;
  reset)
    confirm "Reset database '$NAME' (drop, create, migrate, seed)?" || die "cancelled"
    YES=1 drop_db; create_db; "${PY_RUN[@]}" tt-admin migrate; "${PY_RUN[@]}" tt-admin seed demo ;;
  backup)
    need pg_dump "Install the PostgreSQL 16 client."
    file="${1:-$ROOT/backups/tt-$(date +%Y%m%d-%H%M%S).dump}"; mkdir -p "$(dirname "$file")"
    pg_dump -Fc --no-owner -f "$file" "$(db_libpq_url)"; ok "backup written: $file ($(du -h "$file" | cut -f1))" ;;
  restore)
    need pg_restore "Install the PostgreSQL 16 client."
    file="${1:-}"; [[ -f "$file" ]] || die "usage: scripts/db.sh restore FILE"
    confirm "Replace everything in '$NAME' with $file?" || die "cancelled"
    YES=1 drop_db; create_db
    pg_restore --no-owner --no-comments --exit-on-error -d "$(db_libpq_url)" "$file"; ok "restored $file" ;;
  psql) exec psql "$(db_libpq_url)" "$@" ;;
  verify) "${PY_RUN[@]}" tt-admin verify-projection "${1:-acme}" ;;
  -h|--help|help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown db command '$CMD' (see scripts/db.sh --help)" ;;
esac
