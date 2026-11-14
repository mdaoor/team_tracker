#!/usr/bin/env bash
# Stop components started by ttctl start.
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl stop - stop components (SIGTERM, then SIGKILL after TT_STOP_TIMEOUT=${TT_STOP_TIMEOUT}s)

SYNOPSIS
  $TT_TTCTL stop [COMPONENT...] [--with-db]

OPTIONS
  COMPONENT    server worker web gateway desktop slack (default: everything that runs; clients first, API last)
  --with-db    also stop the database service (native: OS service; docker: the db container; external: nothing)

EXAMPLES
  $TT_TTCTL stop
  $TT_TTCTL stop web
  $TT_TTCTL stop --with-db
  $TT_TTCTL --dry-run stop

SEE ALSO
  $GUIDE/04-run-and-stop.md
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"

WITH_DB=0; NAMES=()
for a in "$@"; do case "$a" in --with-db) WITH_DB=1 ;; -*) usage_fail "unknown option '$a' for stop" ;; *) NAMES+=("$a") ;; esac; done
if [[ ${#NAMES[@]} -eq 0 || "${NAMES[0]}" == "all" ]]; then
  COMPONENTS=("${OPTIONAL_COMPONENTS[@]}" web worker server); EXPLICIT=0
else
  resolve_components "${NAMES[@]}"; EXPLICIT=1
fi
stopped=0
for c in "${COMPONENTS[@]}"; do
  if pid="$(running_pid "$c")"; then
    info "stopping $c (pid $pid)"
    if dry_run; then say "would stop $c (pid $pid)"; continue; fi
    kill_tree "$pid"; rm -f "$(pid_file "$c")"
    printf '=== %s stopped\n' "$(date -Is)" >> "$(log_file "$c")" 2>/dev/null || true
    ok "$c stopped"; stopped=$((stopped + 1))
  elif [[ $EXPLICIT -eq 1 ]]; then ok "$c was not running"; fi
done
[[ $stopped -eq 0 && $EXPLICIT -eq 0 ]] && ok "nothing was running"
[[ $WITH_DB -eq 1 ]] && "$ROOT/scripts/db.sh" stop
exit 0
