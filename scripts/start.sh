#!/usr/bin/env bash
# Start components in the background (dev / UAT / single-host). Logs: .run/logs/<component>.log, PIDs: .run/<component>.pid
#
#   scripts/start.sh                 server + worker + web            (same as "all")
#   scripts/start.sh server web      only those
#   scripts/start.sh gateway         LLM gateway on :8700 (local Ollama only by default; TT_GATEWAY_MODE=config for the YAML)
#   scripts/start.sh desktop         desktop palette / personal tracker preview on :1420
#   scripts/start.sh slack           Slack bot (needs SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET, TT_SLACK_LINK_SECRET)
#   scripts/start.sh --foreground server   run one component attached to the terminal (Ctrl-C stops it)
#
# Components:  server   API (uvicorn)            http://$TT_HOST:$TT_PORT   (docs at /docs, health /readyz)
#              worker   background jobs (Procrastinate): daily logs, governance, recurrence, Jira, exports
#              web      web console (Vite)       http://localhost:$TT_WEB_PORT
#              gateway  LLM gateway (tt-llm)     http://127.0.0.1:$TT_GATEWAY_PORT
#              desktop  desktop palette preview  http://localhost:$TT_DESKTOP_PORT
#              slack    Slack bot (socket mode)
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1

FOREGROUND=0; NAMES=()
for a in "$@"; do
  case "$a" in
    -f|--foreground) FOREGROUND=1 ;; -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) NAMES+=("$a") ;;
  esac
done
mapfile -t COMPONENTS < <(resolve_components "${NAMES[@]+"${NAMES[@]}"}")
[[ -d .venv && -d node_modules ]] || die "dependencies are not installed - run scripts/setup.sh first"
mkdir -p "$LOG_DIR"

# The command line of each component.
component_cmd() {
  case "$1" in
    server)
      local reload=(); [[ "$TT_RELOAD" == "1" ]] && reload=(--reload --reload-dir apps --reload-dir packages)
      echo "${PY_RUN[*]} uvicorn tt_server.main:create_app --factory --host $TT_HOST --port $TT_PORT ${reload[*]}" ;;
    worker) echo "${PY_RUN[*]} python -m tt_worker" ;;
    web) echo "pnpm --filter @tt/web dev --port $TT_WEB_PORT --strictPort" ;;
    desktop) echo "pnpm --filter @tt/desktop dev" ;;
    gateway)
      if [[ "${TT_GATEWAY_MODE:-local}" == "config" ]]; then
        echo "${PY_RUN[*]} tt-llm serve --config ${TT_LLM_CONFIG:-apps/llm_gateway/llm_gateway.example.yaml} --host ${TT_GATEWAY_HOST:-127.0.0.1} --port $TT_GATEWAY_PORT"
      else
        echo "${PY_RUN[*]} tt-llm serve --local-only --port $TT_GATEWAY_PORT"
      fi ;;
    slack) echo "${PY_RUN[*]} tt-slack" ;;
  esac
}

preflight() {
  case "$1" in
    server|worker)
      local h p; h="$(db_part host)"; p="$(db_part port)"
      if have pg_isready && ! pg_isready -q -h "$h" -p "$p"; then
        die "Postgres is not reachable on $h:$p - run: scripts/ttctl db start   (then db create / migrate if new)"
      fi ;;
    web) [[ -f apps/presentation/dist/presentation.html ]] || { info "building the deck viewer first"; pnpm --filter @tt/presentation build >/dev/null; } ;;
    slack)
      local v; for v in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_SIGNING_SECRET TT_SLACK_LINK_SECRET; do
        [[ -n "${!v:-}" ]] || die "slack: $v is not set (put it in .env) - see docs/client/slack.md"; done
      export TT_SERVER_URL="${TT_SERVER_URL:-$TT_API_URL}" ;;
  esac
}

start_one() {
  local c="$1" pid port cmd url log
  if pid="$(running_pid "$c")"; then ok "$c already running (pid $pid)"; return 0; fi
  port="$(component_port "$c")"
  if [[ -n "$port" ]] && port_in_use "$port"; then
    die "$c: port $port is already in use by another program (scripts/ttctl status, or change the port in .env)"
  fi
  preflight "$c"
  cmd="$(component_cmd "$c")"; log="$(log_file "$c")"
  if [[ $FOREGROUND -eq 1 ]]; then info "$c (foreground): $cmd"; exec bash -c "exec $cmd"; fi
  printf '\n=== %s start: %s\n' "$(date -Is)" "$cmd" >> "$log"
  # setsid: own process group, so stop.sh can end uv/pnpm and all their children together
  setsid bash -c "exec $cmd" >> "$log" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$(pid_file "$c")"
  url="$(component_url "$c")"
  if [[ -n "$url" ]]; then
    if wait_http "$url" "${TT_START_TIMEOUT:-90}" "$pid"; then ok "$c started (pid $pid) - ${url%/readyz}"
    else
      err "$c did not become healthy at $url - last log lines:"; tail -n 25 "$log" >&2 || true
      kill_tree "$pid" 5; rm -f "$(pid_file "$c")"; return 1
    fi
  else
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then ok "$c started (pid $pid) - log: ${log#"$ROOT"/}"
    else err "$c exited at once - last log lines:"; tail -n 25 "$log" >&2 || true; rm -f "$(pid_file "$c")"; return 1; fi
  fi
}

[[ $FOREGROUND -eq 1 && ${#COMPONENTS[@]} -ne 1 ]] && die "--foreground takes exactly one component"
rc=0
for c in "${COMPONENTS[@]}"; do start_one "$c" || rc=1; done
if [[ $rc -eq 0 && " ${COMPONENTS[*]} " == *" web "* ]]; then
  echo; echo "Open http://localhost:$TT_WEB_PORT/login   (dev: 'Development sign-in', workspace acme, handle @mahmoud)"
fi
exit $rc
