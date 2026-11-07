#!/usr/bin/env bash
# Run the whole stack in Docker (docker-compose.yml): Postgres, API (:8000), worker, web (:5173).
#
#   scripts/docker.sh up [--llm] [--tls]   build and start in the background (--llm adds gateway + Ollama, --tls the HTTPS server)
#   scripts/docker.sh down                 stop and remove the containers (data volume kept)
#   scripts/docker.sh stop | start         pause / resume the containers without removing them
#   scripts/docker.sh restart [SERVICE]    restart all or one service (db, server, worker, web, llm-gateway, ollama)
#   scripts/docker.sh ps                   container status
#   scripts/docker.sh logs [-f] [SERVICE]  logs
#   scripts/docker.sh seed [--reset]       load the demo workspace "acme" into the container database
#   scripts/docker.sh migrate              run migrations in the server container
#   scripts/docker.sh shell [SERVICE]      a shell in a container (default server)
#   scripts/docker.sh psql                 psql in the db container
#   scripts/docker.sh reset                down + delete the database volume (asks first; --yes to skip)
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1
[[ "${1:-}" =~ ^(-h|--help|help)$ ]] && { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
need docker "Install Docker Desktop / Docker Engine with the compose plugin."
docker compose version >/dev/null 2>&1 || die "the 'docker compose' plugin is missing"
docker info >/dev/null 2>&1 || die "the Docker daemon is not running (start Docker Desktop, or: sudo systemctl start docker)"

CMD="${1:-ps}"; shift || true
PROFILES=(); REST=(); YES=0
for a in "$@"; do
  case "$a" in --llm) PROFILES+=(--profile llm) ;; --tls) PROFILES+=(--profile tls) ;; --yes|-y) YES=1 ;; *) REST+=("$a") ;; esac
done
DC=(docker compose "${PROFILES[@]+"${PROFILES[@]}"}")
[[ -f .env ]] || { cp .env.example .env; ok ".env created from .env.example"; }

case "$CMD" in
  up)
    "${DC[@]}" up --build -d "${REST[@]+"${REST[@]}"}"
    info "waiting for the API"
    wait_http "http://127.0.0.1:8000/readyz" 180 || { err "API not healthy - scripts/docker.sh logs server"; exit 1; }
    ok "API http://localhost:8000 (docs /docs)"
    info "waiting for the web console (first start runs pnpm install, a few minutes)"
    wait_http "http://127.0.0.1:5173/" 600 && ok "web http://localhost:5173/login" || warn "web not up yet - scripts/docker.sh logs -f web"
    echo "Load demo data once: scripts/docker.sh seed" ;;
  down) "${DC[@]}" --profile llm --profile tls down ;;
  stop) "${DC[@]}" --profile llm --profile tls stop ;;
  start) "${DC[@]}" start ;;
  restart) "${DC[@]}" restart "${REST[@]+"${REST[@]}"}" ;;
  ps|status) "${DC[@]}" --profile llm --profile tls ps ;;
  logs) "${DC[@]}" --profile llm --profile tls logs --tail 200 "${REST[@]+"${REST[@]}"}" ;;
  seed) "${DC[@]}" exec server tt-admin seed demo "${REST[@]+"${REST[@]}"}" ;;
  migrate) "${DC[@]}" exec server tt-admin migrate ;;
  shell) "${DC[@]}" exec "${REST[0]:-server}" sh ;;
  psql) "${DC[@]}" exec db psql -U tt -d tt ;;
  reset)
    if [[ $YES -ne 1 ]]; then read -r -p "Delete the containers AND the database volume? [y/N] " r; [[ "$r" =~ ^[Yy]$ ]] || die "cancelled"; fi
    "${DC[@]}" --profile llm --profile tls down -v; ok "containers and volumes removed" ;;
  -h|--help|help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown docker command '$CMD' (see scripts/docker.sh --help)" ;;
esac
