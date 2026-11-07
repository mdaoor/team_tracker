#!/usr/bin/env bash
# Show which components run, their PID, port and health.
#
#   scripts/status.sh          table of all components + database
#   scripts/status.sh --json   the same as JSON (for monitoring scripts)
source "$(dirname "$0")/lib/common.sh"
JSON=0; [[ "${1:-}" == "--json" ]] && JSON=1

rows=(); any_down=0
for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do
  pid="$(running_pid "$c" || true)"; port="$(component_port "$c")"; url="$(component_url "$c")"
  if [[ -n "$pid" ]]; then
    state="running"; health="-"
    if [[ -n "$url" ]]; then http_ok "$url" && health="healthy" || health="unhealthy"; fi
  else
    state="stopped"; health="-"
    if [[ -n "$port" ]] && port_in_use "$port"; then state="external"; health="port $port used by another process"; fi
    for core in "${ALL_COMPONENTS[@]}"; do [[ "$core" == "$c" ]] && any_down=1; done
  fi
  rows+=("$c|$state|${pid:--}|${port:--}|$health")
done

dbstate="down"; h="$(db_part host)"; p="$(db_part port)"
if have pg_isready; then pg_isready -q -h "$h" -p "$p" && dbstate="up"; elif port_in_use "$p"; then dbstate="up"; fi

if [[ $JSON -eq 1 ]]; then
  printf '{"database":{"host":"%s","port":%s,"state":"%s"},"components":[' "$h" "$p" "$dbstate"
  sep=""; for r in "${rows[@]}"; do IFS='|' read -r c s pid port hl <<<"$r"
    printf '%s{"name":"%s","state":"%s","pid":"%s","port":"%s","health":"%s"}' "$sep" "$c" "$s" "$pid" "$port" "$hl"; sep=","; done
  printf ']}\n'
else
  printf '%-9s %-9s %-8s %-6s %s\n' COMPONENT STATE PID PORT HEALTH
  for r in "${rows[@]}"; do IFS='|' read -r c s pid port hl <<<"$r"
    case "$s" in running) col="$C_GRN" ;; external) col="$C_YLW" ;; *) col="$C_DIM" ;; esac
    printf '%-9s %s%-9s%s %-8s %-6s %s\n' "$c" "$col" "$s" "$C_OFF" "$pid" "$port" "$hl"; done
  printf '%-9s %-9s %-8s %-6s %s\n' database "$dbstate" - "$p" "$h"
fi
exit $any_down
