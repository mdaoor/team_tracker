#!/usr/bin/env bash
# Database control (PostgreSQL 16).
source "$(dirname "$0")/lib/common.sh"
source "$ROOT/scripts/lib/prereqs.sh"

usage() {
  cat <<EOF
ttctl db - create, migrate, seed, back up and restore the PostgreSQL database

SYNOPSIS
  $TT_TTCTL db SUBCOMMAND [ARGS] [--db native|docker|external]

SUBCOMMANDS
  start | stop        start/stop the database service (native: systemd/service/pg_ctl/Homebrew; docker: the compose "db"
                      container; external: nothing to do)
  status              server reachable? SERVER version (SHOW server_version_num), extensions, migration revision
  check               like status, but exits non-zero with an explanation when something is wrong
  create              role + database from TT_DATABASE_URL and the extensions $TT_PG_EXTENSIONS (needs a superuser:
                      sudo -u postgres locally, or TT_DB_ADMIN_URL for a remote server)
  migrate             alembic upgrade head + job-queue schema (tt-admin migrate)
  seed [--reset]      demo workspace "acme"
  setup               start + create + check + migrate + seed
  reset | drop        destructive; asks first (--yes to skip); refuses while server/worker run
  backup [FILE]       pg_dump -Fc (default backups/tt-<time>.dump)
  restore FILE        replace the database with a dump
  psql [ARGS]         SQL shell on the app database
  verify [WORKSPACE]  event log vs tables (tt-admin verify-projection), default acme

DATABASE MODES (--db, or TT_DB_MODE in .env)
  native     PostgreSQL $TT_PG_MAJOR installed on this host (ttctl prereqs install postgresql --system)
  docker     the "db" service of docker-compose.yml (postgres:$TT_PG_MAJOR)
  external   a server you run elsewhere; TT_DATABASE_URL points at it; TT_DB_ADMIN_URL lets "create" make the role/db

CLIENT VERSION
  Only backup, restore and psql use a client program, and it must be >= the server ($TT_PG_MAJOR). An older system psql
  (e.g. 10 on RHEL 8) is fine for everything else; these three then use .tools/pgsql, /usr/pgsql-$TT_PG_MAJOR or the
  postgres:$TT_PG_MAJOR container, in that order.

EXAMPLES
  $TT_TTCTL db setup
  $TT_TTCTL db status --db external
  $TT_TTCTL db backup /var/backups/tt/before-upgrade.dump
  $TT_TTCTL --yes db restore backups/tt-20261114-0900.dump
  $TT_TTCTL db psql -c 'select count(*) from work_items'

SEE ALSO
  $GUIDE/05-database.md
EOF
}
TT_PASSTHROUGH_WORDS="psql"
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1

CMD="${1:-status}"; shift || true
REST=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) [[ $# -ge 2 ]] || usage_fail "--db needs native, docker or external"; TT_DB_MODE="$2"; shift ;;
    --db=*) TT_DB_MODE="${1#*=}" ;;
    *) REST+=("$1") ;;
  esac; shift
done
set -- "${REST[@]+"${REST[@]}"}"
MODE="${TT_DB_MODE:-native}"; [[ "$MODE" == local ]] && MODE=native
case "$MODE" in native|docker|external) ;; *) usage_fail "--db must be native, docker or external, not '$MODE'" ;; esac
export TT_DB_MODE="$MODE"
USER_="$(db_part user)"; PASS="$(db_part password)"; HOST="$(db_part host)"; PORT="$(db_part port)"; NAME="$(db_part name)"
detail "database mode=$MODE host=$HOST port=$PORT name=$NAME user=$USER_"

pg_bin_dir() { local b; b="$(pg_server_bin 2>/dev/null)" && dirname "$b"; }

# A superuser SQL session for create/drop.
admin_psql() {
  export PGOPTIONS="${PGOPTIONS:---client-min-messages=warning}"
  local psql_bin; psql_bin="$(pg_bin_dir 2>/dev/null)/psql"; [[ -x "$psql_bin" ]] || psql_bin=psql
  if [[ -n "${TT_DB_ADMIN_URL:-}" ]]; then run "$psql_bin" "$TT_DB_ADMIN_URL" -v ON_ERROR_STOP=1 "$@"
  elif [[ "$MODE" == docker ]]; then run docker compose exec -T db psql -U "$USER_" -d postgres -v ON_ERROR_STOP=1 "$@"
  elif [[ "$MODE" == external ]]; then
    fail TT-E205 "creating a role or database on an external server needs an admin connection" "TT_DB_ADMIN_URL is not set" \
      "set TT_DB_ADMIN_URL=postgresql://postgres:PASSWORD@$HOST:$PORT/postgres in .env, or ask the DBA to run the SQL in $GUIDE/05-database.md#create-by-hand"
  elif [[ "$(id -u)" -eq 0 ]] && id postgres >/dev/null 2>&1; then run runuser -u postgres -- "$psql_bin" -v ON_ERROR_STOP=1 "$@"
  elif have sudo && id postgres >/dev/null 2>&1; then run sudo -u postgres "$psql_bin" -v ON_ERROR_STOP=1 "$@"
  else run "$psql_bin" -d postgres -v ON_ERROR_STOP=1 "$@"   # Homebrew: the current user is the superuser
  fi
}
db_up() { if have pg_isready; then probe pg_isready -q -h "$HOST" -p "$PORT" >/dev/null; else port_in_use "$PORT"; fi; }

# ---------- server check (the SERVER's version and extensions, not the client's) ------------------------------------
dbcheck_json() {
  local py="$ROOT/.venv/bin/python"; [[ -x "$py" ]] || { echo '{"error_kind":"no_driver"}'; return; }
  probe "$py" "$ROOT/scripts/lib/dbcheck.py"
}
jget() { "$(tt_python)" -c 'import json,sys;d=json.loads(sys.argv[1]);v=d
for k in sys.argv[2].split("."): v=(v or {}).get(k) if isinstance(v,dict) else None
print("" if v is None else v)' "$1" "$2" 2>/dev/null; }

# db_check [strict]: report; with strict, fail with the matching TT-E code
db_check() {
  local strict="${1:-}" j kind ver missing=() e
  j="$(dbcheck_json)"; kind="$(jget "$j" error_kind)"
  case "$kind" in
    "")
      ver="$(jget "$j" server_version_num)"
      ok "connected to '$NAME' on $HOST:$PORT as '$USER_' - server PostgreSQL $(jget "$j" server_version)"
      if [[ -n "$ver" && "$ver" -lt $((TT_PG_MAJOR * 10000)) ]]; then
        [[ -n "$strict" ]] && fail TT-E202 "the database server on $HOST:$PORT is PostgreSQL $(jget "$j" server_version)" \
          "Team Tracker needs PostgreSQL $TT_PG_MAJOR or newer (server_version_num=$ver)" \
          "install PostgreSQL $TT_PG_MAJOR: $(tt_cmd prereqs install postgresql --system), or point TT_DATABASE_URL at a $TT_PG_MAJOR server" \
          "install from the bundle: $(tt_cmd bundle install FILE --with-db)"
        warn "server is older than $TT_PG_MAJOR"
      fi
      for e in $TT_PG_EXTENSIONS; do
        case "$(jget "$j" "extensions.$e")" in
          installed) detail "extension $e installed" ;; available) detail "extension $e available (created by db create/migrate)" ;;
          *) missing+=("$e") ;;
        esac
      done
      if [[ ${#missing[@]} -gt 0 ]]; then
        [[ -n "$strict" ]] && fail TT-E203 "extensions ${missing[*]} are not available on the server" \
          "the PostgreSQL contrib modules are not installed next to the server" \
          "install $(pg_contrib_pkg) on the database host, then: $(tt_cmd db create)"
        warn "missing extensions: ${missing[*]} (install $(pg_contrib_pkg))"
      else ok "extensions $TT_PG_EXTENSIONS available"; fi
      local rev; rev="$(jget "$j" revision)"
      if [[ -n "$rev" ]]; then ok "migration revision $rev"; else warn "not migrated yet ($(tt_cmd db migrate))"; fi
      return 0 ;;
    no_driver)
      warn "Python environment not installed yet - only a TCP check is possible"
      if db_up; then ok "something answers on $HOST:$PORT"; return 0; fi
      [[ -n "$strict" ]] && db_unreachable "nothing answers on $HOST:$PORT"
      return 1 ;;
    unreachable) [[ -n "$strict" ]] && db_unreachable "$(jget "$j" error)"; warn "database unreachable: $(jget "$j" error)"; return 1 ;;
    auth)
      [[ -n "$strict" ]] && fail TT-E201 "the server on $HOST:$PORT refused user '$USER_'" "$(jget "$j" error)" \
        "create the role and database: $(tt_cmd db create)   (or correct the user/password in TT_DATABASE_URL in .env)"
      warn "login refused: $(jget "$j" error)"; return 1 ;;
    no_database)
      [[ -n "$strict" ]] && fail TT-E205 "database '$NAME' does not exist on $HOST:$PORT" "$(jget "$j" error)" "$(tt_cmd db create)"
      warn "database '$NAME' does not exist ($(tt_cmd db create))"; return 1 ;;
    *) [[ -n "$strict" ]] && fail TT-E200 "cannot use the database at $HOST:$PORT" "$(jget "$j" error)" "check TT_DATABASE_URL in .env; $(tt_cmd -v db status) shows the probe"
       warn "$(jget "$j" error)"; return 1 ;;
  esac
}
db_unreachable() {
  local why="$1" fix
  case "$MODE" in
    native) if pg_server_bin >/dev/null; then fix="$(tt_cmd db start)"; else fix="$(tt_cmd prereqs install postgresql --system)   (or use --db docker / --db external)"; fi ;;
    docker) fix="$(tt_cmd db start --db docker)" ;;
    external) fix="check that $HOST:$PORT is the right server, it is running and the firewall lets this host in (TT_DATABASE_URL in .env)" ;;
  esac
  fail TT-E200 "the database server is not reachable at $HOST:$PORT (mode: $MODE)" "$why" "$fix" \
    "$( [[ "$MODE" == native ]] && echo "$(tt_cmd bundle install FILE --with-db) installs PostgreSQL from the bundle" || echo "$fix")"
}

# ---------- service control ------------------------------------------------------------------------------------------
start_db() {
  if db_up; then ok "PostgreSQL answers on $HOST:$PORT"; return; fi
  case "$MODE" in
    external) db_unreachable "external mode: this script does not start the server" ;;
    docker)
      have docker && probe docker info >/dev/null 2>&1 || fail TT-E108 "--db docker needs Docker" "docker is missing or its daemon is not running" "start Docker (sudo systemctl start docker), or use --db native"
      run docker compose up -d db ;;
    native)
      local s=() bin; bin="$(pg_server_bin || true)"
      [[ -n "$bin" ]] || fail TT-E206 "PostgreSQL $TT_PG_MAJOR is not installed on this host" "no postgres $TT_PG_MAJOR binary in the usual places" \
        "$(tt_cmd prereqs install postgresql --system)   (or: $(tt_cmd setup --with-db --db docker))" "$(tt_cmd bundle install FILE --with-db)"
      local pre; pre="$(sudo_prefix)"
      if have systemctl && [[ -d /run/systemd/system ]] && systemctl list-unit-files 2>/dev/null | grep -qE "^postgresql(-$TT_PG_MAJOR)?\.service"; then
        local unit; unit="$(systemctl list-unit-files 2>/dev/null | grep -oE "^postgresql(-$TT_PG_MAJOR)?\.service" | tail -1)"
        s=(${pre:+sudo} systemctl start "$unit")
      elif have pg_ctlcluster && [[ -d "/etc/postgresql/$TT_PG_MAJOR/main" ]]; then s=(${pre:+sudo} pg_ctlcluster "$TT_PG_MAJOR" main start)
      elif have brew; then s=(brew services start "postgresql@$TT_PG_MAJOR")
      else
        local data="${TT_PGDATA:-/var/lib/pgsql/$TT_PG_MAJOR/data}"; [[ -d "$data" ]] || data=/var/lib/pgsql/data
        [[ -f "$data/PG_VERSION" ]] || fail TT-E206 "PostgreSQL is installed but has no data directory" "looked in $data (no PG_VERSION)" \
          "initialise it: $(sudo_prefix)$(dirname "$bin")/initdb -D $data  (as the postgres user), or set TT_PGDATA"
        s=(runuser -u postgres -- "$(dirname "$bin")/pg_ctl" -D "$data" -l "$data/../pg_ctl.log" -w start)
        [[ -n "$pre" ]] && s=(sudo -u postgres "$(dirname "$bin")/pg_ctl" -D "$data" -l "$data/../pg_ctl.log" -w start)
      fi
      [[ -n "$pre" ]] && say "  starting PostgreSQL needs root: ${s[*]}"
      run "${s[@]}" ;;
  esac
  dry_run && return 0
  local i; for i in $(seq 1 60); do db_up && { ok "PostgreSQL up on $HOST:$PORT"; return; }; sleep 0.5; done
  db_unreachable "started, but nothing answers on $HOST:$PORT after 30 s"
}
stop_db() {
  case "$MODE" in
    external) ok "external database: not stopped by ttctl"; return ;;
    docker) run docker compose stop db ;;
    native)
      local pre; pre="$(sudo_prefix)"
      if have systemctl && [[ -d /run/systemd/system ]] && systemctl list-unit-files 2>/dev/null | grep -qE "^postgresql(-$TT_PG_MAJOR)?\.service"; then
        run ${pre:+sudo} systemctl stop "$(systemctl list-unit-files | grep -oE "^postgresql(-$TT_PG_MAJOR)?\.service" | tail -1)"
      elif have pg_ctlcluster && [[ -d "/etc/postgresql/$TT_PG_MAJOR/main" ]]; then run ${pre:+sudo} pg_ctlcluster "$TT_PG_MAJOR" main stop
      elif have brew; then run brew services stop "postgresql@$TT_PG_MAJOR"
      else
        local bin data="${TT_PGDATA:-/var/lib/pgsql/$TT_PG_MAJOR/data}"; bin="$(pg_server_bin)"; [[ -d "$data" ]] || data=/var/lib/pgsql/data
        run runuser -u postgres -- "$(dirname "$bin")/pg_ctl" -D "$data" -m fast stop
      fi ;;
  esac
  ok "PostgreSQL stopped"
}

create_db() {
  if [[ "$MODE" == docker ]]; then ok "the db container creates role '$USER_' and database '$NAME' itself"; return; fi
  if [[ "$MODE" == external && -z "${TT_DB_ADMIN_URL:-}" ]]; then
    db_check strict; ok "external database ready (role and database exist)"; return
  fi
  db_up || db_unreachable "nothing answers on $HOST:$PORT"
  info "creating role '$USER_' and database '$NAME' if missing"
  local sql
  sql="DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$USER_') THEN CREATE ROLE \"$USER_\" LOGIN CREATEDB PASSWORD '${PASS//\'/\'\'}'; END IF; END \$\$;"
  admin_psql -q -c "$sql" || fail TT-E205 "could not create role '$USER_'" "the superuser session failed (output above)" \
    "run it as the postgres superuser, or set TT_DB_ADMIN_URL; SQL in $GUIDE/05-database.md#create-by-hand"
  if ! dry_run && [[ -z "$(admin_psql -tAq -c "SELECT 1 FROM pg_database WHERE datname = '$NAME'" 2>/dev/null)" ]]; then
    admin_psql -q -c "CREATE DATABASE \"$NAME\" OWNER \"$USER_\"" || fail TT-E205 "could not create database '$NAME'" "see the output above" \
      "check that the superuser can create databases; SQL in $GUIDE/05-database.md#create-by-hand"
  fi
  local e args=(); for e in $TT_PG_EXTENSIONS; do args+=(-c "CREATE EXTENSION IF NOT EXISTS $e"); done
  admin_psql -q -d "$NAME" "${args[@]}" || fail TT-E203 "could not create the extensions $TT_PG_EXTENSIONS" \
    "the contrib modules are probably not installed next to the server" "install $(pg_contrib_pkg), then: $(tt_cmd db create)"
  ok "database '$NAME' ready"
}
app_running() { local c; for c in server worker; do running_pid "$c" >/dev/null && return 0; done; return 1; }
drop_db() {
  app_running && fail TT-E207 "the app is running and holds connections to '$NAME'" "server or worker is up" "$(tt_cmd stop), then run this again"
  confirm "Drop database '$NAME' on $HOST:$PORT? All data is lost." || fail TT-E013 "dropping '$NAME' was not confirmed" "you answered no" "$(tt_cmd db drop --yes)"
  if [[ "$MODE" == external && -z "${TT_DB_ADMIN_URL:-}" ]]; then admin_psql; fi
  admin_psql -q -c "DROP DATABASE IF EXISTS \"$NAME\" WITH (FORCE)"
  ok "database '$NAME' dropped"
}

# A client program (pg_dump, pg_restore, psql) at least as new as the server; falls back to the postgres image.
PG_CLIENT=()
pg_client() {
  local tool="$1" want="$TT_PG_MAJOR" c v
  for c in "$TOOLS_DIR/pgsql/bin/$tool" "/usr/pgsql-$want/bin/$tool" "/usr/lib/postgresql/$want/bin/$tool" "$(command -v "$tool" 2>/dev/null || true)"; do
    [[ -n "$c" && -x "$c" ]] || continue
    v="$(probe "$c" --version | first_version || true)"
    if [[ -n "$v" && "${v%%.*}" -ge "$want" ]]; then PG_CLIENT=("$c"); detail "using $c ($v)"; return 0; fi
    detail "skipping $c: version $v < $want"
  done
  if have docker && probe docker info >/dev/null 2>&1; then
    PG_CLIENT=(docker run --rm -i --network host -v "$ROOT:$ROOT" -w "$PWD" "postgres:$want" "$tool")
    detail "using the postgres:$want container for $tool"; return 0
  fi
  local found; found="$(command -v "$tool" 2>/dev/null || true)"
  fail TT-E109 "$tool $want or newer is needed for '$CMD'" \
    "${found:+found $found ($(probe "$found" --version | first_version)) - older than the server. }an older client cannot read or write a PostgreSQL $want dump reliably" \
    "$(tt_cmd prereqs install psql --system)   (installs the $want client next to the old one)" \
    "$(tt_cmd bundle install FILE --with-db) provides it from the bundle"
}

case "$CMD" in
  start) start_db ;;
  stop) stop_db ;;
  status) if db_up || [[ "$MODE" == external ]]; then db_check || exit $E_RUNTIME; else warn "PostgreSQL is not answering on $HOST:$PORT (mode: $MODE)"; exit $E_RUNTIME; fi ;;
  check) db_check strict ;;
  create) create_db ;;
  migrate) need_python_env; db_check strict; step "migrate the schema" "${PY_RUN[@]}" tt-admin migrate; ok "migrations applied" ;;
  seed) need_python_env; step "seed the demo workspace 'acme'" "${PY_RUN[@]}" tt-admin seed demo "$@"; ok "demo workspace 'acme' loaded" ;;
  setup)
    need_python_env
    start_db; create_db; db_check strict
    step "migrate the schema" "${PY_RUN[@]}" tt-admin migrate
    step "seed the demo workspace 'acme'" "${PY_RUN[@]}" tt-admin seed demo
    ok "database ready: $(tt_cmd start) next" ;;
  drop) drop_db ;;
  reset)
    confirm "Reset database '$NAME' (drop, create, migrate, seed)?" || fail TT-E013 "reset was not confirmed" "you answered no" "$(tt_cmd db reset --yes)"
    TT_YES=1 drop_db; create_db
    step "migrate the schema" "${PY_RUN[@]}" tt-admin migrate
    step "seed the demo workspace 'acme'" "${PY_RUN[@]}" tt-admin seed demo
    ok "database reset" ;;
  backup)
    pg_client pg_dump
    file="${1:-$ROOT/backups/tt-$(date +%Y%m%d-%H%M%S).dump}"; run mkdir -p "$(dirname "$file")"
    case "$file" in /*) ;; *) file="$PWD/$file" ;; esac
    run "${PG_CLIENT[@]}" -Fc --no-owner -f "$file" "$(db_libpq_url)"
    dry_run || ok "backup written: $file ($(du -h "$file" | cut -f1))" ;;
  restore)
    file="${1:-}"; [[ -n "$file" ]] || usage_fail "restore needs a dump file" "usage: $TT_TTCTL db restore FILE"
    [[ -f "$file" ]] || fail TT-E012 "no such dump file: $file" "the path does not exist from $PWD" "ls backups/   then: $(tt_cmd db restore backups/FILE)"
    case "$file" in /*) ;; *) file="$PWD/$file" ;; esac
    pg_client pg_restore
    confirm "Replace everything in '$NAME' with $file?" || fail TT-E013 "restore was not confirmed" "you answered no" "$(tt_cmd db restore "$file" --yes)"
    TT_YES=1 drop_db; create_db
    step "restore $file" "${PG_CLIENT[@]}" --no-owner --no-comments --exit-on-error -d "$(db_libpq_url)" "$file"
    ok "restored $file" ;;
  psql) pg_client psql; exec "${PG_CLIENT[@]}" "$(db_libpq_url)" "$@" ;;
  verify) need_python_env; "${PY_RUN[@]}" tt-admin verify-projection "${1:-acme}" ;;
  *) fail TT-E011 "unknown db subcommand '$CMD'" "subcommands: start stop status check create migrate seed setup reset drop backup restore psql verify" "$TT_TTCTL help db" ;;
esac
