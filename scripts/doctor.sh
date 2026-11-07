#!/usr/bin/env bash
# Check the machine and a running install.
#
#   scripts/doctor.sh                tools, versions, configuration, ports, database, health endpoints
#   scripts/doctor.sh --tools-only   only the tools (used by setup.sh)
#
# Exit code 0 when nothing required is missing.
source "$(dirname "$0")/lib/common.sh"
TOOLS_ONLY=0; [[ "${1:-}" == "--tools-only" ]] && TOOLS_ONLY=1
FAIL=0

ver_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }   # ver_ge HAVE WANT
check_tool() {   # name min_version version_command install_hint required(1/0)
  local name="$1" min="$2" cmd="$3" hint="$4" req="${5:-1}" v
  if ! have "$name"; then
    if [[ $req -eq 1 ]]; then err "$name missing - $hint"; FAIL=1; else warn "$name not found (optional) - $hint"; fi
    return
  fi
  v="$(eval "$cmd" 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+)+' | head -1 || true)"
  if [[ -n "$min" && -n "$v" ]] && ! ver_ge "$v" "$min"; then
    if [[ $req -eq 1 ]]; then err "$name $v is older than $min - $hint"; FAIL=1; else warn "$name $v is older than $min"; fi
  else
    ok "$name ${v:-?}"
  fi
}

title "Tools"
check_tool python3 3.12 "python3 --version" "install Python 3.12+"
check_tool uv 0.4 "uv --version" "curl -LsSf https://astral.sh/uv/install.sh | sh"
check_tool node 22 "node --version" "install Node.js 22 (https://nodejs.org or nvm)"
check_tool pnpm 9 "pnpm --version" "corepack enable && corepack prepare pnpm@9.15.0 --activate"
check_tool curl "" "curl --version" "install curl"
check_tool psql 16 "psql --version" "PostgreSQL 16 client (apt install postgresql-16 / brew install postgresql@16), or use Docker" 0
check_tool docker "" "docker --version" "only needed for scripts/ttctl docker ..." 0
check_tool git "" "git --version" "install git" 0

[[ $TOOLS_ONLY -eq 1 ]] && exit $FAIL

title "Configuration"
if [[ -f "$ROOT/.env" ]]; then ok ".env present"; else warn ".env missing - run scripts/setup.sh (or cp .env.example .env)"; fi
echo "   TT_ENV=${TT_ENV:-dev}  TT_DEV_AUTH=${TT_DEV_AUTH:-0}  API=$TT_HOST:$TT_PORT  web=:$TT_WEB_PORT"
echo "   TT_DATABASE_URL=${TT_DATABASE_URL//:$(db_part password)@/:***@}"
if [[ "${TT_ENV:-dev}" != "dev" && "${TT_DEV_AUTH:-0}" =~ ^(1|true)$ ]]; then err "TT_DEV_AUTH is on outside dev - the server refuses to start"; FAIL=1; fi
[[ -d "$ROOT/.venv" ]] && ok "Python environment (.venv)" || { warn "no .venv - run scripts/setup.sh"; }
[[ -d "$ROOT/node_modules" ]] && ok "Node modules" || { warn "no node_modules - run scripts/setup.sh"; }
[[ -f "$ROOT/apps/presentation/dist/presentation.html" ]] && ok "deck viewer built" || warn "deck viewer not built - scripts/ttctl build presentation"

title "Database"
host="$(db_part host)"; port="$(db_part port)"
if have pg_isready; then
  if pg_isready -q -h "$host" -p "$port"; then ok "Postgres answers on $host:$port"; else warn "Postgres not reachable on $host:$port - scripts/ttctl db start"; fi
elif port_in_use "$port"; then ok "something listens on $port"; else warn "nothing listens on $port"; fi
if have psql && psql "$(db_libpq_url)" -tAc 'select 1' >/dev/null 2>&1; then
  ok "can connect as $(db_part user) to database $(db_part name)"
  if psql "$(db_libpq_url)" -tAc "select to_regclass('public.alembic_version')" 2>/dev/null | grep -q alembic; then
    ok "schema migrated (revision $(psql "$(db_libpq_url)" -tAc 'select version_num from alembic_version' 2>/dev/null))"
  else warn "schema not migrated - scripts/ttctl db migrate"; fi
fi

title "Processes"
run_script status.sh || true

exit $FAIL
