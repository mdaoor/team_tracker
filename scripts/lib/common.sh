# shellcheck shell=bash disable=SC2034
# (SC2034: colours, PY_RUN and the E_* codes are used by the scripts that source this file)
#
# Shared framework for every script in scripts/. Source it, then call tt_init "$@" and use TT_ARGS:
#
#   source "$(dirname "$0")/lib/common.sh"
#   tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
#
# What it gives every script and subcommand:
#   global flags   -v/--verbose  -vv/--trace  -q/--quiet  --dry-run  -y/--yes  --no-color  --log-file PATH  -h/--help
#                  (env: TT_VERBOSE=0|1|2, TT_QUIET, TT_DRY_RUN, TT_YES, NO_COLOR, TT_LOG_FILE) - exported to child scripts
#   a run log      .run/logs/ttctl-<command>-<timestamp>.log with full detail, always; its path is printed on failure
#   step / run     numbered steps that capture output (last 40 lines on failure) or stream it with -v; dry-run aware
#   fail           one error format: WHAT / WHY / FIX / DOCS with a stable TT-Ennn code from lib/errors.tsv
#   exit codes     0 ok, 1 runtime failure, 2 usage, 3 prerequisite missing, 4 blocked because offline, 5 configuration
#   offline guard  TT_OFFLINE=1 refuses network actions with exit 4 (tt_need_network)
#
# Paths:  ROOT repository root;  RUN_DIR .run/ (PIDs);  LOG_DIR .run/logs/;  TOOLS_DIR .tools/ (user-space prerequisites)

set -euo pipefail
set -o errtrace

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="${TT_RUN_DIR:-$ROOT/.run}"
LOG_DIR="$RUN_DIR/logs"
TOOLS_DIR="${TT_TOOLS_DIR:-$ROOT/.tools}"
export ROOT RUN_DIR LOG_DIR TOOLS_DIR

E_OK=0; E_RUNTIME=1; E_USAGE=2; E_PREREQ=3; E_OFFLINE=4; E_CONFIG=5
GUIDE="docs/guide"
TROUBLE="$GUIDE/11-troubleshooting.md"
TT_TTCTL="${TT_TTCTL:-scripts/ttctl}"     # how the user reaches ttctl; messages always name it, never an internal script

# User-space prerequisites installed by "ttctl prereqs install" (or a bundle) come first on PATH.
# (always on PATH, even before they exist, so a tool installed by this very run is found at once)
for _d in "$TOOLS_DIR/pgsql/bin" "$TOOLS_DIR/node/bin" "$TOOLS_DIR/bin"; do
  [[ ":$PATH:" != *":$_d:"* ]] && PATH="$_d:$PATH"
done
unset _d
export PATH
[[ -d "$TOOLS_DIR/python" ]] && export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$TOOLS_DIR/python}"
if [[ -d "$TOOLS_DIR/ms-playwright" ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$TOOLS_DIR/ms-playwright}"
  # the server launches exactly this browser for PDF/PNG export (TT_CHROMIUM), not Playwright's default headless shell
  if [[ -z "${TT_CHROMIUM:-}" ]]; then
    for _c in "$TOOLS_DIR"/ms-playwright/chromium-[0-9]*/chrome-linux*/chrome; do [[ -x "$_c" ]] && export TT_CHROMIUM="$_c"; done
    unset _c
  fi
fi

# ---------- output ---------------------------------------------------------------------------------------------------
_tt_colors() {
  if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RED=$'\e[31m'; C_GRN=$'\e[32m'; C_YLW=$'\e[33m'; C_BLU=$'\e[34m'; C_DIM=$'\e[2m'; C_BLD=$'\e[1m'; C_OFF=$'\e[0m'
  else
    C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_DIM=""; C_BLD=""; C_OFF=""
  fi
}
_tt_colors

_log() { [[ -n "${TT_LOG_FILE:-}" ]] && printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >> "$TT_LOG_FILE" 2>/dev/null; return 0; }
_quiet() { [[ "${TT_QUIET:-0}" == 1 ]]; }
verbose() { [[ "${TT_VERBOSE:-0}" -ge 1 ]]; }
info()  { _log "==> $*"; _quiet || printf '%s==>%s %s\n' "$C_BLU" "$C_OFF" "$*"; }
ok()    { _log " ok $*"; _quiet || printf '%s ok%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn()  { _log "warn $*"; printf '%swarn%s %s\n' "$C_YLW" "$C_OFF" "$*" >&2; }
title() { _log "## $*"; _quiet || printf '\n%s%s%s\n' "$C_BLD" "$*" "$C_OFF"; }
say()   { _log "$*"; _quiet || printf '%s\n' "$*"; }
# detail: always in the log; on screen only with -v
detail() { _log "  · $*"; if verbose; then printf '%s  · %s%s\n' "$C_DIM" "$*" "$C_OFF" >&2; fi; return 0; }

have() { command -v "$1" >/dev/null 2>&1; }

# tt_cmd SUB...  -> the ttctl command line the user should type, e.g. "scripts/ttctl prereqs install pnpm"
tt_cmd() { printf '%s %s' "$TT_TTCTL" "$*"; }

# ---------- errors ---------------------------------------------------------------------------------------------------
_err_field() {   # _err_field CODE exit|summary
  local line; line="$(grep -E "^$1"$'\t' "$ROOT/scripts/lib/errors.tsv" 2>/dev/null | head -1)"
  [[ -n "$line" ]] || { [[ "$2" == exit ]] && echo 1 || echo "Unlisted error"; return; }
  case "$2" in exit) cut -f2 <<<"$line" ;; summary) cut -f3 <<<"$line" ;; esac
}

# fail CODE WHAT WHY FIX [OFFLINE_FIX] [DOCS]
#   Prints the four-line error and exits with the code's exit status from lib/errors.tsv.
#   OFFLINE_FIX replaces FIX when TT_OFFLINE=1. DOCS defaults to the code's troubleshooting section.
fail() {
  local code="$1" what="$2" why="$3" fix="$4" offline_fix="${5:-}" docs="${6:-}" rc
  rc="$(_err_field "$code" exit)"
  [[ "${TT_OFFLINE:-0}" == 1 && -n "$offline_fix" ]] && fix="$offline_fix"
  [[ -n "$docs" ]] || docs="$TROUBLE#$(tr '[:upper:]' '[:lower:]' <<<"$code")"
  {
    printf '%serror %s%s  %s\n' "$C_RED" "$code" "$C_OFF" "$(_err_field "$code" summary)"
    printf '  WHAT  %s\n' "$what"
    printf '  WHY   %s\n' "$why"
    printf '  FIX   %s\n' "$fix"
    printf '  DOCS  %s\n' "$docs"
    [[ -n "${TT_LOG_FILE:-}" ]] && printf '  LOG   %s\n' "${TT_LOG_FILE#"$ROOT"/}"
  } >&2
  _log "error $code what=[$what] why=[$why] fix=[$fix] docs=[$docs] exit=$rc"
  TT_FAILED=1
  # a parent ttctl script (same run log) must not report this failure again
  [[ -n "${TT_LOG_FILE:-}" ]] && : > "$TT_LOG_FILE.reported" 2>/dev/null
  # $$ is the main script's PID even inside $(...): tells the parent's ERR trap this was already reported
  if [[ "${BASH_SUBSHELL:-0}" -gt 0 ]]; then mkdir -p "$RUN_DIR" 2>/dev/null && : > "$RUN_DIR/.reported-$$" 2>/dev/null; fi
  exit "$rc"
}
usage_fail() {   # usage_fail WHAT [WHY]
  fail TT-E010 "$1" "${2:-the arguments do not match the synopsis}" "$TT_TTCTL help ${TT_COMMAND:-}" "" "$TROUBLE#tt-e010"
}
die() { fail TT-E001 "$1" "${2:-see the log}" "re-run with -v for details: ${TT_INVOKED_AS:-$TT_TTCTL} -v"; }

# ERR trap: anything not handled by fail() still gets a WHAT/WHY/FIX/DOCS report.
_tt_on_err() {
  local rc="$1" cmd="$2" src="$3" line="$4"
  [[ "${TT_FAILED:-0}" == 1 ]] && exit "$rc"
  if [[ "${BASH_SUBSHELL:-0}" -gt 0 ]]; then exit "$rc"; fi   # fail() in a subshell already printed the report
  if [[ -f "$RUN_DIR/.reported-$$" ]]; then rm -f "$RUN_DIR/.reported-$$"; exit "$rc"; fi
  # a child ttctl script already printed its WHAT/WHY/FIX/DOCS report: keep its exit code, add nothing
  if [[ -n "${TT_LOG_FILE:-}" && -f "$TT_LOG_FILE.reported" ]]; then exit "$rc"; fi   # let the parent report it with its own context
  TT_FAILED=1
  {
    printf '%serror TT-E001%s  %s\n' "$C_RED" "$C_OFF" "$(_err_field TT-E001 summary)"
    printf '  WHAT  %s\n' "$cmd"
    printf '  WHY   exit code %s at %s:%s%s\n' "$rc" "${src#"$ROOT"/}" "$line" "${TT_STEP_TITLE:+ (step: $TT_STEP_TITLE)}"
    printf '  FIX   re-run with -v to see every command: %s -v\n' "${TT_INVOKED_AS:-$TT_TTCTL}"
    printf '  DOCS  %s#tt-e001\n' "$TROUBLE"
    [[ -n "${TT_LOG_FILE:-}" ]] && printf '  LOG   %s\n' "${TT_LOG_FILE#"$ROOT"/}"
  } >&2
  _log "error TT-E001 cmd=[$cmd] rc=$rc at $src:$line step=[${TT_STEP_TITLE:-}]"
  exit 1
}
trap '_tt_on_err $? "$BASH_COMMAND" "${BASH_SOURCE[0]}" "$LINENO"' ERR

# ---------- global flags, help, log ----------------------------------------------------------------------------------
TT_ARGS=()
# Words after which no more global flags are parsed (their arguments belong to another program), e.g. "client run".
TT_PASSTHROUGH_WORDS=""

_tt_global_help() {
  cat <<EOF

GLOBAL OPTIONS (accepted anywhere, passed on to child scripts)
  -v, --verbose      print every command before it runs, every probe and the effective configuration (secrets masked)
  -vv, --trace       -v plus shell tracing (file:line of every command)
  -q, --quiet        only warnings and errors
  --dry-run          print what would run; change nothing
  -y, --yes          answer yes to every prompt (needed when there is no terminal)
  --no-color         plain output (also NO_COLOR=1)
  --log-file PATH    write the run log here instead of .run/logs/ttctl-<command>-<time>.log
  -h, --help         this help
  Environment: TT_VERBOSE=0|1|2  TT_QUIET=1  TT_DRY_RUN=1  TT_YES=1  TT_OFFLINE=1 (refuse network actions)

EXIT CODES
  0 ok   1 runtime failure   2 usage   3 prerequisite missing   4 blocked because offline   5 configuration
EOF
}

# tt_init "$@": parse global flags, open the run log, print --help (scripts define a usage() function).
tt_init() {
  local a pass=0 want_help=0
  TT_ARGS=()
  while [[ $# -gt 0 ]]; do
    a="$1"; shift
    if [[ $pass -eq 1 ]]; then TT_ARGS+=("$a"); continue; fi
    case "$a" in
      -v|--verbose) TT_VERBOSE=$(( ${TT_VERBOSE:-0} < 1 ? 1 : ${TT_VERBOSE:-0} )) ;;
      -vv|--trace) TT_VERBOSE=2 ;;
      -q|--quiet) TT_QUIET=1 ;;
      --dry-run) TT_DRY_RUN=1 ;;
      -y|--yes) TT_YES=1 ;;
      --no-color|--no-colour) NO_COLOR=1 ;;
      --log-file) [[ $# -gt 0 ]] || usage_fail "--log-file needs a path"; TT_LOG_FILE="$1"; shift ;;
      --log-file=*) TT_LOG_FILE="${a#*=}" ;;
      -h|--help) want_help=1 ;;
      --) pass=1 ;;
      *) TT_ARGS+=("$a")
         if [[ -n "$TT_PASSTHROUGH_WORDS" && ${#TT_ARGS[@]} -eq 1 && " $TT_PASSTHROUGH_WORDS " == *" $a "* ]]; then pass=1; fi ;;
    esac
  done
  export TT_VERBOSE="${TT_VERBOSE:-0}" TT_QUIET="${TT_QUIET:-0}" TT_DRY_RUN="${TT_DRY_RUN:-0}" TT_YES="${TT_YES:-0}"
  [[ -n "${NO_COLOR:-}" ]] && export NO_COLOR
  _tt_colors
  if [[ $want_help -eq 1 ]]; then
    if declare -F usage >/dev/null; then usage; else sed -n '2,/^[^#]/p' "$0" | sed -e '/^[^#]/d' -e 's/^# \{0,1\}//'; fi
    _tt_global_help
    exit 0
  fi
  : "${TT_COMMAND:=$(basename "$0" .sh)}"
  : "${TT_INVOKED_AS:=$TT_TTCTL $TT_COMMAND}"
  export TT_COMMAND TT_INVOKED_AS TT_TTCTL
  if [[ -z "${TT_LOG_FILE:-}" ]]; then
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    TT_LOG_FILE="$LOG_DIR/ttctl-${TT_COMMAND// /-}-$(date +%Y%m%d-%H%M%S)-$$.log"
  else
    mkdir -p "$(dirname "$TT_LOG_FILE")" 2>/dev/null || true
  fi
  export TT_LOG_FILE
  if [[ -z "${TT_LOG_STARTED:-}" ]]; then
    export TT_LOG_STARTED=1
    rm -f "$TT_LOG_FILE.reported" 2>/dev/null
    { echo "=== $(date -Is 2>/dev/null || date) $TT_INVOKED_AS ${TT_ARGS[*]+"${TT_ARGS[*]}"}"
      echo "    cwd=$PWD user=$(id -un 2>/dev/null) host=$(uname -n) os=$(tt_os_id 2>/dev/null || uname -s)"
      echo "    verbose=$TT_VERBOSE dry_run=$TT_DRY_RUN yes=$TT_YES offline=${TT_OFFLINE:-0}"; } >> "$TT_LOG_FILE" 2>/dev/null || TT_LOG_FILE=""
  fi
  if [[ "$TT_VERBOSE" -ge 2 ]]; then
    export PS4='+ ${BASH_SOURCE[0]#'"$ROOT"'/}:${LINENO}: '
    set -x
  fi
  if verbose && [[ -z "${TT_CONFIG_SHOWN:-}" ]]; then export TT_CONFIG_SHOWN=1; tt_show_config; fi
}

# Effective configuration with secrets masked (shown with -v, always in the log).
tt_mask() {   # tt_mask KEY VALUE
  local k="$1" v="$2"
  if [[ "$k" =~ (SECRET|PASSWORD|TOKEN|_KEY$|KEYFILE) && -n "$v" ]]; then echo "***"
  elif [[ "$v" =~ ^([a-z+]+://[^:/@]+:)[^@]*(@.*)$ ]]; then echo "${BASH_REMATCH[1]}***${BASH_REMATCH[2]}"
  else echo "$v"; fi
}
tt_show_config() {
  local k
  detail "effective configuration (from environment and .env; secrets masked):"
  while IFS= read -r k; do detail "  $k=$(tt_mask "$k" "${!k}")"; done < <(compgen -e | grep -E '^(TT_|UV_|PIP_|npm_config_|PLAYWRIGHT_|COREPACK_)' | sort)
  detail "  PATH=$PATH"
}

# ---------- running commands -----------------------------------------------------------------------------------------
dry_run() { [[ "${TT_DRY_RUN:-0}" == 1 ]]; }
_q() { local out="" a; for a in "$@"; do out+="$(printf '%q' "$a") "; done; printf '%s' "${out% }"; }

# run CMD...  - a state-changing command: shown with -v, skipped (printed) with --dry-run, logged always.
run() {
  _log "+ $(_q "$@")"
  if dry_run; then _quiet || printf '%swould run:%s %s\n' "$C_YLW" "$C_OFF" "$(_q "$@")"; return 0; fi
  if verbose; then printf '%s+ %s%s\n' "$C_DIM" "$(_q "$@")" "$C_OFF" >&2; fi
  "$@"
}
# probe CMD... - a read-only check: never skipped by --dry-run; command and raw output go to the log (and screen with -v).
probe() {
  local out rc=0 ef err
  ef="$(mktemp)"; out="$("$@" 2>"$ef")" || rc=$?
  err="$(cat "$ef")"; rm -f "$ef"
  detail "probe: $(_q "$@") -> rc=$rc${out:+ | ${out//$'\n'/ ⏎ }}${err:+ | stderr: ${err//$'\n'/ ⏎ }}"
  printf '%s' "$out"
  return $rc
}

# step "title" CMD...  - a numbered step. Output is captured (quiet/normal) or streamed (-v).
# On failure: the last 40 lines and a TT-E002 report naming the step, the exact command, its exit code and the log.
TT_STEP_N=0
step() {
  local t="$1"; shift
  TT_STEP_N=$((TT_STEP_N + 1)); TT_STEP_TITLE="$t"
  _log "--- step $TT_STEP_N: $t: $(_q "$@")"
  _quiet || printf '%s[%d]%s %s\n' "$C_BLD" "$TT_STEP_N" "$C_OFF" "$t"
  if dry_run; then _quiet || printf '    %swould run:%s %s\n' "$C_YLW" "$C_OFF" "$(_q "$@")"; TT_STEP_TITLE=""; return 0; fi
  local tmp rc=0; tmp="$(mktemp)"
  if verbose; then
    printf '%s+ %s%s\n' "$C_DIM" "$(_q "$@")" "$C_OFF" >&2
    ( "$@" ) 2>&1 | tee -a "$tmp" || rc=${PIPESTATUS[0]}
  else
    ( "$@" ) > "$tmp" 2>&1 || rc=$?
  fi
  [[ -n "${TT_LOG_FILE:-}" ]] && cat "$tmp" >> "$TT_LOG_FILE"
  if [[ $rc -ne 0 ]]; then
    # A ttctl script that failed already printed its WHAT/WHY/FIX/DOCS report: show it and keep its exit code.
    if grep -q 'error TT-E[0-9]' "$tmp"; then
      if ! verbose; then sed -n '/error TT-E[0-9]/,$p' "$tmp" >&2; fi
      rm -f "$tmp"; TT_FAILED=1; exit "$rc"
    fi
    if ! verbose; then printf '%s--- last 40 lines of output ---%s\n' "$C_DIM" "$C_OFF" >&2; tail -n 40 "$tmp" >&2; fi
    rm -f "$tmp"
    fail TT-E002 "step $TT_STEP_N \"$t\" failed: $(_q "$@")" "it exited with code $rc (output above and in the log)" \
      "fix the cause shown above, then run again: ${TT_INVOKED_AS:-$TT_TTCTL}  (add -v to stream the output)"
  fi
  rm -f "$tmp"; TT_STEP_TITLE=""
}

# confirm "question"  -> 0 yes / 1 no. --yes answers yes; with no terminal and no --yes: fail TT-E013 (exit 2).
confirm() {
  local q="$1" def="${2:-n}" r
  if [[ "${TT_YES:-0}" == 1 ]]; then _log "confirm: $q -> yes (--yes)"; return 0; fi
  if [[ ! -t 0 ]]; then
    fail TT-E013 "$q" "no terminal to answer the question and --yes was not given" "${TT_INVOKED_AS:-$TT_TTCTL} --yes   (after reading what it will do; --dry-run shows it)"
  fi
  if [[ "$def" == y ]]; then read -r -p "$q [Y/n] " r; [[ -z "$r" || "$r" =~ ^[Yy] ]]
  else read -r -p "$q [y/N] " r; [[ "$r" =~ ^[Yy] ]]; fi
}

# ---------- offline guard --------------------------------------------------------------------------------------------
# tt_need_network WHAT [BUNDLE_COMPONENT] [BUNDLE_FLAG] - refuse with exit 4 when TT_OFFLINE=1.
tt_need_network() {
  [[ "${TT_OFFLINE:-0}" == 1 ]] || return 0
  local what="$1" comp="${2:-}" flag="${3:-}"
  if [[ -n "$comp" ]]; then
    fail TT-E401 "$what needs the network, and this host is offline (TT_OFFLINE=1)" \
      "the bundle installed here has no \"$comp\" component" \
      "on the connected machine: $(tt_cmd bundle create --target "${TT_BUNDLE_TARGET:-<target>}" "$flag"), copy it here, then: $(tt_cmd bundle install FILE)"
  fi
  fail TT-E400 "$what needs the network, and this host is offline (TT_OFFLINE=1)" \
    "offline mode refuses every download so nothing half-installs" \
    "use the bundle's copy (see $GUIDE/03-install-airgapped.md), or unset TT_OFFLINE on a connected host"
}

# ---------- configuration --------------------------------------------------------------------------------------------
# Load .env (KEY=VALUE lines) without overriding variables already set in the shell.
load_env() {
  local f="${1:-$ROOT/.env}" line key val
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"; val="${line#*=}"; key="${key// /}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$val" == \"*\" || "$val" == \'*\' ]]; then val="${val:1:${#val}-2}"; fi
    [[ -n "${!key+x}" ]] || export "$key=$val"
  done < "$f"
}
load_env
# A bundle install records offline mode and paths here (sourced after .env so the shell still wins).
[[ -f "$ROOT/.ttctl.env" ]] && load_env "$ROOT/.ttctl.env"
if [[ "${TT_OFFLINE:-0}" == 1 ]]; then
  export TT_OFFLINE UV_OFFLINE=1 PIP_NO_INDEX=1 npm_config_offline=true COREPACK_ENABLE_NETWORK=0 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
fi

: "${TT_DATABASE_URL:=postgresql+psycopg://tt:tt@localhost:5432/tt}"
: "${TT_HOST:=127.0.0.1}"
: "${TT_PORT:=8000}"
: "${TT_WEB_PORT:=5173}"
: "${TT_DESKTOP_PORT:=1420}"
: "${TT_GATEWAY_PORT:=8700}"
: "${TT_RELOAD:=1}"
: "${TT_STOP_TIMEOUT:=15}"
export TT_DATABASE_URL TT_HOST TT_PORT TT_WEB_PORT TT_DESKTOP_PORT TT_GATEWAY_PORT
export TT_API_URL="${TT_API_URL:-http://127.0.0.1:$TT_PORT}"

# Python entry points: "uv run" normally; offline (bundle) installs use the prepared .venv without syncing.
if [[ "${TT_OFFLINE:-0}" == 1 || "${TT_NO_SYNC:-0}" == 1 ]]; then
  PY_RUN=(uv run --project "$ROOT" --no-sync --offline)
else
  PY_RUN=(uv run --project "$ROOT")
fi
export UV_NO_PROGRESS=1

# A Python for small helper jobs: the project's .venv first (always there on bundle installs), then the system's.
tt_python() {
  local p; for p in "$ROOT/.venv/bin/python" "$(command -v python3 2>/dev/null)" /usr/libexec/platform-python; do
    [[ -n "$p" && -x "$p" ]] && { echo "$p"; return 0; }; done
  return 1
}

# Pieces of TT_DATABASE_URL: db_part user|password|host|port|name
db_part() {
  local u="${TT_DATABASE_URL#*://}" auth hostpart rest
  if [[ "$u" == *@* ]]; then auth="${u%%@*}"; rest="${u#*@}"; else auth=""; rest="$u"; fi
  hostpart="${rest%%/*}"
  case "$1" in
    user) echo "${auth%%:*}" ;;
    password) [[ "$auth" == *:* ]] && "$(tt_python)" -c 'import sys,urllib.parse as u;print(u.unquote(sys.argv[1]))' "${auth#*:}" 2>/dev/null || echo "" ;;
    host) local h="${hostpart%%:*}"; echo "${h:-localhost}" ;;
    port) [[ "$hostpart" == *:* ]] && echo "${hostpart##*:}" || echo 5432 ;;
    name) local n="${rest#*/}"; echo "${n%%\?*}" ;;
  esac
}
db_libpq_url() { printf '%s' "${TT_DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"; }

# ---------- OS -------------------------------------------------------------------------------------------------------
# tt_os_id -> e.g. rhel8 rhel9 ubuntu22.04 ubuntu24.04 debian12 macos unknown
tt_os_id() {
  if [[ "$(uname -s)" == Darwin ]]; then echo macos; return; fi
  [[ -r /etc/os-release ]] || { echo unknown; return; }
  local ID="" VERSION_ID="" ID_LIKE=""
  # shellcheck disable=SC1091
  . /etc/os-release
  case "$ID" in
    rhel|rocky|almalinux|centos|ol) echo "rhel${VERSION_ID%%.*}" ;;
    ubuntu) echo "ubuntu$VERSION_ID" ;;
    debian) echo "debian${VERSION_ID%%.*}" ;;
    *) if [[ " $ID_LIKE " == *" rhel "* ]]; then echo "rhel${VERSION_ID%%.*}"; elif [[ " $ID_LIKE " == *" debian "* ]]; then echo "debian${VERSION_ID%%.*}"; else echo "$ID${VERSION_ID:+$VERSION_ID}"; fi ;;
  esac
}
tt_os_supported() { case "$(tt_os_id)" in rhel8|rhel9|ubuntu22.04|ubuntu24.04|debian12|macos) return 0 ;; *) return 1 ;; esac; }
tt_arch() { case "$(uname -m)" in x86_64|amd64) echo x86_64 ;; aarch64|arm64) echo aarch64 ;; *) uname -m ;; esac; }
# Root helper: never calls sudo silently. sudo_cmd prints the prefix to use ("" as root, "sudo " otherwise).
sudo_prefix() { if [[ "$(id -u)" -eq 0 ]]; then echo ""; else echo "sudo "; fi; }

# ---------- components -----------------------------------------------------------------------------------------------
ALL_COMPONENTS=(server worker web)
OPTIONAL_COMPONENTS=(gateway desktop slack)

component_port() {
  case "$1" in server) echo "$TT_PORT" ;; web) echo "$TT_WEB_PORT" ;; desktop) echo "$TT_DESKTOP_PORT" ;; gateway) echo "$TT_GATEWAY_PORT" ;; *) echo "" ;; esac
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
# resolve_components NAME... -> sets COMPONENTS (all = the default three); unknown names are a usage error.
resolve_components() {
  local c; COMPONENTS=()
  if [[ $# -eq 0 || "$1" == "all" ]]; then COMPONENTS=("${ALL_COMPONENTS[@]}"); return; fi
  for c in "$@"; do
    is_component "$c" || usage_fail "unknown component '$c'" "known components: ${ALL_COMPONENTS[*]} ${OPTIONAL_COMPONENTS[*]}"
    COMPONENTS+=("$c")
  done
}
pid_file() { echo "$RUN_DIR/$1.pid"; }
log_file() { echo "$LOG_DIR/$1.log"; }
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
# HTTP check: curl, or the project's Python where curl is not installed (minimal air-gapped hosts)
http_ok() {
  if have curl; then curl -fsS -m 3 -o /dev/null "$1" 2>/dev/null; return; fi
  local py; py="$(tt_python)" && "$py" -c 'import sys,urllib.request as u;u.urlopen(sys.argv[1],timeout=3)' "$1" >/dev/null 2>&1
}
wait_http() {
  local url="$1" secs="$2" pid="${3:-}" i
  for ((i = 0; i < secs * 2; i++)); do
    http_ok "$url" && return 0
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    sleep 0.5
  done
  return 1
}
kill_tree() {
  local pid="$1" timeout="${2:-$TT_STOP_TIMEOUT}" i
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for ((i = 0; i < timeout * 2; i++)); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.5; done
  warn "pid $pid did not stop in ${timeout}s; sending SIGKILL"
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}
run_script() { local s="$1"; shift; "$ROOT/scripts/$s" "$@"; }

# Dependencies installed? (.venv for Python; node_modules only matters where Node is used)
need_python_env() {
  [[ -x "$ROOT/.venv/bin/python" ]] && return 0
  fail TT-E030 "the Python environment (.venv) is missing" "setup has not run on this checkout (or ran with another prefix)" \
    "$(tt_cmd setup)" "$(tt_cmd bundle install FILE)   (the bundle installs .venv from its wheelhouse)"
}
