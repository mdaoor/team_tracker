#!/usr/bin/env bash
# Control the production services installed by "ttctl prod install" (systemd).
source "$(dirname "$0")/../lib/common.sh"
usage() {
  cat <<EOF
ttctl prod - control the production services (systemd units tt-server, tt-worker, tt-llm-gateway)

SYNOPSIS
  $TT_TTCTL prod start | stop | restart | status | enable | disable
  $TT_TTCTL prod reload                 re-read TLS certificates without dropping connections (tls mode)
  $TT_TTCTL prod logs [-f] [UNIT]       journalctl for the units (default: all)
  $TT_TTCTL prod health [URL]           GET /readyz (http://127.0.0.1:8000, or https://127.0.0.1:8443 in tls mode)
  $TT_TTCTL prod install ...            see: $TT_TTCTL help prod install
  $TT_TTCTL prod upgrade | rollback | releases   see: $TT_TTCTL help prod upgrade

EXAMPLES
  $TT_TTCTL prod status
  sudo $TT_TTCTL prod restart
  $TT_TTCTL prod logs -f tt-worker
  $TT_TTCTL prod health

SEE ALSO
  $GUIDE/06-production.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
CMD="${1:-status}"; shift || true
have systemctl || fail TT-E502 "the production services are systemd units" "systemctl is not on PATH" "on hosts without systemd use $TT_TTCTL start/stop/status"
UNITS=(tt-server tt-worker)
systemctl cat tt-llm-gateway >/dev/null 2>&1 && UNITS+=(tt-llm-gateway)
SUDO=(); [[ "$(id -u)" -ne 0 ]] && SUDO=(sudo)
health_url() {
  if [[ -n "${1:-}" ]]; then echo "$1"; return; fi
  if systemctl cat tt-server 2>/dev/null | grep -q 'tt-admin serve'; then echo "https://127.0.0.1:8443/readyz"; else echo "http://127.0.0.1:8000/readyz"; fi
}
case "$CMD" in
  start|stop|restart|enable|disable) run "${SUDO[@]}" systemctl "$CMD" "${UNITS[@]}"; ok "$CMD: ${UNITS[*]}" ;;
  reload) run "${SUDO[@]}" systemctl reload tt-server; ok "tt-server reloaded" ;;
  status)
    rc=0
    for u in "${UNITS[@]}"; do s="$(systemctl is-active "$u" 2>/dev/null || true)"; printf '%-16s %s\n' "$u" "$s"; [[ "$s" == active ]] || rc=1; done
    url="$(health_url)"
    if curl -fsSk -m 5 "$url" >/dev/null 2>&1; then ok "health $url"; else warn "health check failed: $url ($TT_TTCTL prod logs tt-server)"; rc=1; fi
    exit $rc ;;
  health)
    url="$(health_url "${1:-}")"
    curl -fsSk -m 5 "$url" || fail TT-E306 "the API does not answer at $url" "tt-server is down or still starting" "$TT_TTCTL prod status; $TT_TTCTL prod logs tt-server"
    echo ;;
  logs)
    f=(); sel=()
    for a in "$@"; do if [[ "$a" == -f ]]; then f=(-f); else sel+=(-u "$a"); fi; done
    [[ ${#sel[@]} -eq 0 ]] && for u in "${UNITS[@]}"; do sel+=(-u "$u"); done
    exec "${SUDO[@]}" journalctl "${sel[@]}" -n 200 "${f[@]+"${f[@]}"}" ;;
  *) fail TT-E011 "unknown prod command '$CMD'" "commands: install start stop restart status reload logs health enable disable upgrade rollback releases" "$TT_TTCTL help prod" ;;
esac
