#!/usr/bin/env bash
# Run the whole stack in Docker (docker-compose.yml).
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl docker - the whole stack in Docker: PostgreSQL, API (:8000), worker, web (:5173)

SYNOPSIS
  $TT_TTCTL docker up [--llm] [--tls] | down | stop | start | restart [SERVICE] | ps
  $TT_TTCTL docker logs [-f] [SERVICE] | seed [--reset] | migrate | shell [SERVICE] | psql | reset

OPTIONS
  --llm    add the LLM gateway and Ollama containers     --tls    add the API with its own HTTPS on :8443
  Services: db server worker web llm-gateway ollama. reset deletes the database volume (asks; --yes to skip).
  Offline hosts: load the images first (bundle install does it when the bundle has --with-docker-images).

EXAMPLES
  $TT_TTCTL docker up
  $TT_TTCTL docker seed
  $TT_TTCTL docker logs -f server
  $TT_TTCTL --yes docker reset

SEE ALSO
  $GUIDE/02-install-online.md#b-docker
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
have docker || fail TT-E108 "Docker is not installed" "the docker command is not on PATH" "install Docker Engine with the compose plugin ($(tt_cmd prereqs install docker --system) prints how), or use the local install: $(tt_cmd setup)"
probe docker compose version >/dev/null 2>&1 || fail TT-E108 "the docker compose plugin is missing" "'docker compose version' failed" "install docker-compose-plugin from the Docker repository"
probe docker info >/dev/null 2>&1 || fail TT-E108 "the Docker daemon is not running" "'docker info' cannot reach it" "start Docker Desktop, or: $(sudo_prefix)systemctl start docker"
CMD="${1:-ps}"; shift || true
PROFILES=(); REST=()
for a in "$@"; do case "$a" in --llm) PROFILES+=(--profile llm) ;; --tls) PROFILES+=(--profile tls) ;; *) REST+=("$a") ;; esac; done
DC=(docker compose "${PROFILES[@]+"${PROFILES[@]}"}")
ALL=(docker compose --profile llm --profile tls)
[[ -f .env ]] || { run cp .env.example .env; ok ".env created from .env.example"; }
case "$CMD" in
  up)
    [[ "${TT_OFFLINE:-0}" == 1 ]] && tt_need_network "docker compose build (pulls base images)" docker-images --with-docker-images
    run "${DC[@]}" up --build -d "${REST[@]+"${REST[@]}"}"
    dry_run && exit 0
    info "waiting for the API"
    wait_http "http://127.0.0.1:8000/readyz" 180 || fail TT-E301 "the API container did not become healthy" "no answer on :8000/readyz after 180 s" "$(tt_cmd docker logs server)"
    ok "API http://localhost:8000 (docs /docs)"
    info "waiting for the web console (first start runs pnpm install, a few minutes)"
    if wait_http "http://127.0.0.1:5173/" 600; then ok "web http://localhost:5173/login"; else warn "web not up yet - $(tt_cmd docker logs -f web)"; fi
    say "Load demo data once: $(tt_cmd docker seed)" ;;
  down) run "${ALL[@]}" down ;;
  stop) run "${ALL[@]}" stop ;;
  start) run "${DC[@]}" start ;;
  restart) run "${DC[@]}" restart "${REST[@]+"${REST[@]}"}" ;;
  ps|status) "${ALL[@]}" ps ;;
  logs) "${ALL[@]}" logs --tail 200 "${REST[@]+"${REST[@]}"}" ;;
  seed) run "${DC[@]}" exec server tt-admin seed demo "${REST[@]+"${REST[@]}"}" ;;
  migrate) run "${DC[@]}" exec server tt-admin migrate ;;
  shell) "${DC[@]}" exec "${REST[0]:-server}" sh ;;
  psql) "${DC[@]}" exec db psql -U tt -d tt ;;
  reset)
    confirm "Delete the containers AND the database volume?" || fail TT-E013 "reset was not confirmed" "you answered no" "$(tt_cmd docker reset --yes)"
    run "${ALL[@]}" down -v; ok "containers and volumes removed" ;;
  *) fail TT-E011 "unknown docker command '$CMD'" "commands: up down stop start restart ps logs seed migrate shell psql reset" "$TT_TTCTL help docker" ;;
esac
