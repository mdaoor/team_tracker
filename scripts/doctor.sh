#!/usr/bin/env bash
# Check the machine and a running install.
source "$(dirname "$0")/lib/common.sh"
source "$ROOT/scripts/lib/prereqs.sh"
usage() {
  cat <<EOF2
ttctl doctor - check tools, configuration, database and processes

SYNOPSIS
  $TT_TTCTL doctor [--tools-only]

OPTIONS
  --tools-only   only the prerequisites (same as: ttctl prereqs check)
  Exit code 0 when nothing required is missing; 3 when a prerequisite is missing; 1 when the database or a process is down.

EXAMPLES
  $TT_TTCTL doctor
  $TT_TTCTL -v doctor
  $TT_TTCTL doctor --tools-only

SEE ALSO
  $GUIDE/11-troubleshooting.md   $TT_TTCTL support-bundle
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
TOOLS_ONLY=0
for a in "$@"; do case "$a" in --tools-only) TOOLS_ONLY=1 ;; *) usage_fail "unknown option '$a' for doctor" ;; esac; done
rc=0
title "Prerequisites"
"$ROOT/scripts/prereqs.sh" check || rc=$?
[[ $TOOLS_ONLY -eq 1 ]] && exit $rc

title "Configuration"
if [[ -f "$ROOT/.env" ]]; then ok ".env present"; else warn ".env missing - $(tt_cmd setup) creates it"; fi
say "   TT_ENV=${TT_ENV:-dev}  TT_DEV_AUTH=${TT_DEV_AUTH:-0}  API=$TT_HOST:$TT_PORT  web=:$TT_WEB_PORT  db mode=${TT_DB_MODE:-native}  offline=${TT_OFFLINE:-0}"
say "   TT_DATABASE_URL=$(tt_mask TT_DATABASE_URL "$TT_DATABASE_URL")"
if [[ "${TT_ENV:-dev}" != "dev" && "${TT_DEV_AUTH:-0}" =~ ^(1|true)$ ]]; then
  warn "TT_DEV_AUTH is on outside dev - the server refuses to start"; rc=5
fi
[[ -x "$ROOT/.venv/bin/python" ]] && ok "Python environment (.venv)" || { warn "no .venv - $(tt_cmd setup)"; [[ $rc -eq 0 ]] && rc=3; }
if [[ -f "$ROOT/apps/web/dist/index.html" ]]; then ok "web console built (apps/web/dist)"; elif [[ -d "$ROOT/node_modules" ]]; then ok "Node modules (web console runs with Vite)"; else warn "web console neither built nor installable - $(tt_cmd setup)"; fi
[[ -f "$ROOT/apps/presentation/dist/presentation.html" ]] && ok "deck viewer built" || warn "deck viewer not built - $(tt_cmd build presentation)"

title "Database"
"$ROOT/scripts/db.sh" status || { [[ $rc -eq 0 ]] && rc=1; }

title "Processes"
"$ROOT/scripts/status.sh" || true
exit $rc
