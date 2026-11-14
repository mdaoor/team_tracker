#!/usr/bin/env bash
# Show component logs.
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl logs - show, follow or clear logs (.run/logs/)

SYNOPSIS
  $TT_TTCTL logs [-f] [-n N] [COMPONENT...]
  $TT_TTCTL logs --runs            list the ttctl run logs (one per command, newest last)
  $TT_TTCTL logs --clear           delete all logs (components must be stopped)

OPTIONS
  -f, --follow   follow (Ctrl-C to leave); all component logs when none is named
  -n N           last N lines (default 100)
  --runs         list ttctl-<command>-<time>.log files written by every ttctl run
  --clear        delete the logs

EXAMPLES
  $TT_TTCTL logs server
  $TT_TTCTL logs -f worker web
  $TT_TTCTL logs -n 500 server
  $TT_TTCTL logs --runs

SEE ALSO
  $GUIDE/04-run-and-stop.md#logs
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
FOLLOW=0; N=100; NAMES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--follow) FOLLOW=1 ;;
    -n) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || usage_fail "-n needs a number of lines"; N="$2"; shift ;;
    --runs) ls -1tr "$LOG_DIR"/ttctl-*.log 2>/dev/null | sed "s#^$ROOT/##" | tail -n "$N"; exit 0 ;;
    --clear)
      for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do
        running_pid "$c" >/dev/null && fail TT-E303 "cannot clear logs while $c runs" "its log file is open" "$(tt_cmd stop), then $(tt_cmd logs --clear)"
      done
      run find "$LOG_DIR" -name '*.log' ! -path "$TT_LOG_FILE" -delete; ok "logs cleared"; exit 0 ;;
    -*) usage_fail "unknown option '$1' for logs" ;;
    *) NAMES+=("$1") ;;
  esac; shift
done
files=()
if [[ ${#NAMES[@]} -eq 0 ]]; then
  for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do [[ -f "$(log_file "$c")" ]] && files+=("$(log_file "$c")"); done
else
  resolve_components "${NAMES[@]}"
  for c in "${COMPONENTS[@]}"; do files+=("$(log_file "$c")"); done
fi
[[ ${#files[@]} -gt 0 ]] || fail TT-E310 "no component logs yet in ${LOG_DIR#"$ROOT"/}" "nothing has been started with ttctl on this checkout" "$(tt_cmd start)"
for f in "${files[@]}"; do
  [[ -f "$f" ]] || fail TT-E310 "no log for $(basename "$f" .log) yet" "it has not been started with ttctl" "$(tt_cmd start "$(basename "$f" .log)")"
done
if [[ $FOLLOW -eq 1 ]]; then exec tail -n "$N" -F "${files[@]}"; else tail -n "$N" "${files[@]}"; fi
