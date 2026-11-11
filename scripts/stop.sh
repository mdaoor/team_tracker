#!/usr/bin/env bash
# Stop components started by scripts/start.sh (graceful SIGTERM, SIGKILL after TT_STOP_TIMEOUT seconds).
#
#   scripts/stop.sh                  stop everything that is running (all known components)
#   scripts/stop.sh web worker       stop only those
#   scripts/stop.sh --with-db        ...and stop the local Postgres service too
source "$(dirname "$0")/lib/common.sh"

WITH_DB=0; NAMES=()
for a in "$@"; do
  case "$a" in --with-db) WITH_DB=1 ;; -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) NAMES+=("$a") ;; esac
done
if [[ ${#NAMES[@]} -eq 0 || "${NAMES[0]}" == "all" ]]; then
  COMPONENTS=("${OPTIONAL_COMPONENTS[@]}" web worker server)     # reverse start order: clients first, API last
else
  mapfile -t COMPONENTS < <(resolve_components "${NAMES[@]}")
fi

stopped=0
for c in "${COMPONENTS[@]}"; do
  if pid="$(running_pid "$c")"; then
    info "stopping $c (pid $pid)"
    kill_tree "$pid"
    rm -f "$(pid_file "$c")"
    printf '=== %s stopped\n' "$(date -Is)" >> "$(log_file "$c")" 2>/dev/null || true
    ok "$c stopped"; stopped=$((stopped + 1))
  elif [[ ${#NAMES[@]} -gt 0 ]]; then
    ok "$c was not running"
  fi
done
[[ $stopped -eq 0 && ${#NAMES[@]} -eq 0 ]] && ok "nothing was running"
if [[ $WITH_DB -eq 1 ]]; then run_script db.sh stop; fi
exit 0
