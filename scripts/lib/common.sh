# shellcheck shell=bash disable=SC2034
# (SC2034: the colours and PY_RUN are used by the scripts that source this file)
# Shared helpers for every script in scripts/. Source it; do not run it.
#
#   ROOT      repository root
#   RUN_DIR   .run/          PID files of background processes started by scripts/start.sh
#   LOG_DIR   .run/logs/     one log file per component
#
# Every tunable below can be overridden from the environment or from .env (the same file the server reads).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="${TT_RUN_DIR:-$ROOT/.run}"
LOG_DIR="$RUN_DIR/logs"
export ROOT RUN_DIR LOG_DIR

# ---------- output ---------------------------------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RED=$'\e[31m'; C_GRN=$'\e[32m'; C_YLW=$'\e[33m'; C_BLU=$'\e[34m'; C_DIM=$'\e[2m'; C_BLD=$'\e[1m'; C_OFF=$'\e[0m'
else
  C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_DIM=""; C_BLD=""; C_OFF=""
fi
info()  { printf '%s==>%s %s\n' "$C_BLU" "$C_OFF" "$*"; }
ok()    { printf '%s ok%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn()  { printf '%swarn%s %s\n' "$C_YLW" "$C_OFF" "$*" >&2; }
err()   { printf '%serror%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
die()   { err "$*"; exit 1; }
title() { printf '\n%s%s%s\n' "$C_BLD" "$*" "$C_OFF"; }

have() { command -v "$1" >/dev/null 2>&1; }
need() { have "$1" || die "'$1' is not installed. $2"; }

# ---------- configuration --------------------------------------------------------------------------------------------
# Load .env (KEY=VALUE lines; comments and blanks ignored) without overriding variables already set in the shell.
load_env() {
  local f="$ROOT/.env" line key val
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    key="${key// /}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    # strip one level of surrounding quotes
    if [[ "$val" == \"*\" || "$val" == \'*\' ]]; then val="${val:1:${#val}-2}"; fi
    [[ -n "${!key+x}" ]] || export "$key=$val"
  done < "$f"
}
load_env

: "${TT_DATABASE_URL:=postgresql+psycopg://tt:tt@localhost:5432/tt}"
: "${TT_HOST:=127.0.0.1}"          # bind address of the API (dev). Use 0.0.0.0 to reach it from other machines.
: "${TT_PORT:=8000}"               # API port
: "${TT_WEB_PORT:=5173}"           # web console (Vite dev server)
: "${TT_DESKTOP_PORT:=1420}"       # desktop palette preview (fixed by apps/desktop/vite.config.ts)
: "${TT_GATEWAY_PORT:=8700}"       # LLM gateway
: "${TT_RELOAD:=1}"                # 1 = API restarts on code change (dev); 0 = no reload
: "${TT_STOP_TIMEOUT:=15}"         # seconds to wait for a graceful stop before SIGKILL
export TT_DATABASE_URL TT_HOST TT_PORT TT_WEB_PORT TT_DESKTOP_PORT TT_GATEWAY_PORT
export TT_API_URL="${TT_API_URL:-http://127.0.0.1:$TT_PORT}"

# Every Python entry point runs inside the uv workspace.
PY_RUN=(uv run --project "$ROOT")

# Pieces of TT_DATABASE_URL: db_part user|password|host|port|name
db_part() {
  python3 - "$TT_DATABASE_URL" "$1" <<'PY'
import sys, urllib.parse as u
p = u.urlparse(sys.argv[1].replace("postgresql+psycopg://", "postgresql://", 1))
print({"user": p.username or "", "password": u.unquote(p.password or ""), "host": p.hostname or "localhost",
       "port": str(p.port or 5432), "name": (p.path or "/").lstrip("/").split("?")[0]}[sys.argv[2]])
PY
}
# libpq-style URL (psql / pg_dump do not understand "+psycopg")
db_libpq_url() { printf '%s' "${TT_DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"; }

# ---------- components -----------------------------------------------------------------------------------------------
# Components managed by start/stop/status, in start order.
ALL_COMPONENTS=(server worker web)
OPTIONAL_COMPONENTS=(gateway desktop slack)

component_port() {
  case "$1" in
    server) echo "$TT_PORT" ;; web) echo "$TT_WEB_PORT" ;; desktop) echo "$TT_DESKTOP_PORT" ;;
    gateway) echo "$TT_GATEWAY_PORT" ;; *) echo "" ;;
  esac
}
component_url() {
  case "$1" in
    server) echo "http://127.0.0.1:$TT_PORT/readyz" ;;
    web) echo "http://127.0.0.1:$TT_WEB_PORT/" ;;
    desktop) echo "http://127.0.0.1:$TT_DESKTOP_PORT/" ;;
    gateway) echo "http://127.0.0.1:$TT_GATEWAY_PORT/healthz" ;;
    *) echo "" ;;
  esac
}
is_component() { local c; for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do [[ "$c" == "$1" ]] && return 0; done; return 1; }

# Expand "all" and validate names. Prints one name per line.
resolve_components() {
  local c
  if [[ $# -eq 0 || "$1" == "all" ]]; then printf '%s\n' "${ALL_COMPONENTS[@]}"; return; fi
  for c in "$@"; do is_component "$c" || die "unknown component '$c' (known: ${ALL_COMPONENTS[*]} ${OPTIONAL_COMPONENTS[*]})"; echo "$c"; done
}

pid_file() { echo "$RUN_DIR/$1.pid"; }
log_file() { echo "$LOG_DIR/$1.log"; }

# PID of a running component, or nothing.
running_pid() {
  local f pid; f="$(pid_file "$1")"
  [[ -f "$f" ]] || return 1
  pid="$(cat "$f" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then echo "$pid"; return 0; fi
  rm -f "$f"; return 1
}

port_in_use() {
  local port="$1"
  if have ss; then ss -ltnH "( sport = :$port )" 2>/dev/null | grep -q . && return 0 || return 1; fi
  if have lsof; then lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0 || return 1; fi
  (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && return 0 || return 1
}

http_ok() { curl -fsS -m 3 -o /dev/null "$1" 2>/dev/null; }

# wait_http URL SECONDS [PID]  - poll until the URL answers 2xx, the PID dies, or time runs out.
wait_http() {
  local url="$1" secs="$2" pid="${3:-}" i
  for ((i = 0; i < secs * 2; i++)); do
    http_ok "$url" && return 0
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    sleep 0.5
  done
  return 1
}

# Stop a process and its whole process group (uv/pnpm spawn children).
kill_tree() {
  local pid="$1" timeout="${2:-$TT_STOP_TIMEOUT}" i
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for ((i = 0; i < timeout * 2; i++)); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.5
  done
  warn "pid $pid did not stop in ${timeout}s; sending SIGKILL"
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

# Run a script from scripts/ (used by the dispatcher and by scripts that call each other).
run_script() { local s="$1"; shift; "$ROOT/scripts/$s" "$@"; }
