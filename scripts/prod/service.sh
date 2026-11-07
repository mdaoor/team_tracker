#!/usr/bin/env bash
# Control the production services installed by scripts/prod/install.sh (systemd).
#
#   scripts/prod/service.sh start | stop | restart | status
#   scripts/prod/service.sh reload            re-read TLS certificates without dropping connections (tls mode)
#   scripts/prod/service.sh logs [-f] [unit]  journalctl for tt-server / tt-worker / tt-llm-gateway (default: all)
#   scripts/prod/service.sh enable | disable  start on boot or not
#   scripts/prod/service.sh health [URL]      GET /readyz (default http://127.0.0.1:8000, or https://127.0.0.1:8443 in tls mode)
source "$(dirname "$0")/../lib/common.sh"
CMD="${1:-status}"; shift || true

UNITS=(tt-server tt-worker)
systemctl list-unit-files tt-llm-gateway.service >/dev/null 2>&1 && systemctl cat tt-llm-gateway >/dev/null 2>&1 && UNITS+=(tt-llm-gateway)
SUDO=(); [[ "$(id -u)" -ne 0 ]] && SUDO=(sudo)

health_url() {
  if [[ -n "${1:-}" ]]; then echo "$1"; return; fi
  if systemctl cat tt-server 2>/dev/null | grep -q 'tt-admin serve'; then echo "https://127.0.0.1:8443/readyz"; else echo "http://127.0.0.1:8000/readyz"; fi
}

case "$CMD" in
  start) "${SUDO[@]}" systemctl start "${UNITS[@]}"; ok "started: ${UNITS[*]}" ;;
  stop) "${SUDO[@]}" systemctl stop "${UNITS[@]}"; ok "stopped: ${UNITS[*]}" ;;
  restart) "${SUDO[@]}" systemctl restart "${UNITS[@]}"; ok "restarted: ${UNITS[*]}" ;;
  reload) "${SUDO[@]}" systemctl reload tt-server; ok "tt-server reloaded" ;;
  enable|disable) "${SUDO[@]}" systemctl "$CMD" "${UNITS[@]}" ;;
  status)
    for u in "${UNITS[@]}"; do printf '%-16s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"; done
    url="$(health_url)"; if curl -fsSk -m 5 "$url" >/dev/null 2>&1; then ok "health $url"; else warn "health check failed: $url"; fi ;;
  health) url="$(health_url "${1:-}")"; curl -fsSk -m 5 "$url"; echo ;;
  logs)
    f=(); sel=()
    for a in "$@"; do [[ "$a" == -f ]] && f=(-f) || sel+=(-u "$a"); done
    [[ ${#sel[@]} -eq 0 ]] && for u in "${UNITS[@]}"; do sel+=(-u "$u"); done
    exec "${SUDO[@]}" journalctl "${sel[@]}" -n 200 "${f[@]+"${f[@]}"}" ;;
  -h|--help|help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command '$CMD' (see --help)" ;;
esac
