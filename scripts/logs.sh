#!/usr/bin/env bash
# Show component logs (.run/logs/<component>.log).
#
#   scripts/logs.sh server           last 100 lines of the API log
#   scripts/logs.sh -f server        follow it (Ctrl-C to leave)
#   scripts/logs.sh -f               follow all logs together
#   scripts/logs.sh -n 500 worker    last 500 lines
#   scripts/logs.sh --clear          delete all log files (components must be stopped)
source "$(dirname "$0")/lib/common.sh"

FOLLOW=0; N=100; NAMES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--follow) FOLLOW=1 ;; -n) N="$2"; shift ;;
    --clear) for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do running_pid "$c" >/dev/null && die "stop $c first"; done
             rm -f "$LOG_DIR"/*.log; ok "logs cleared"; exit 0 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) NAMES+=("$1") ;;
  esac; shift
done

files=()
if [[ ${#NAMES[@]} -eq 0 ]]; then
  shopt -s nullglob; files=("$LOG_DIR"/*.log); shopt -u nullglob
else
  mapfile -t comps < <(resolve_components "${NAMES[@]}")
  for c in "${comps[@]}"; do files+=("$(log_file "$c")"); done
fi
[[ ${#files[@]} -gt 0 ]] || die "no logs yet in ${LOG_DIR#"$ROOT"/} (start something with scripts/ttctl start)"
for f in "${files[@]}"; do [[ -f "$f" ]] || die "no log for $(basename "$f" .log) yet"; done
if [[ $FOLLOW -eq 1 ]]; then exec tail -n "$N" -F "${files[@]}"; else tail -n "$N" "${files[@]}"; fi
