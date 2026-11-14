#!/usr/bin/env bash
# Start components in the background.
source "$(dirname "$0")/lib/common.sh"

usage() {
  cat <<EOF
ttctl start - start components in the background (dev, UAT, single host)

SYNOPSIS
  $TT_TTCTL start [COMPONENT...]
  $TT_TTCTL run COMPONENT                 (= start --foreground: attached to the terminal, Ctrl-C stops it)

COMPONENTS
  server   API (uvicorn)             http://$TT_HOST:$TT_PORT   (/docs, /healthz, /readyz)
  worker   background jobs (Procrastinate): daily logs, governance, recurrence, Jira, exports
  web      web console               http://localhost:$TT_WEB_PORT  (Vite with Node; prebuilt apps/web/dist without Node)
  gateway  LLM gateway (tt-llm)      http://127.0.0.1:$TT_GATEWAY_PORT  (local models only unless TT_GATEWAY_MODE=config)
  desktop  desktop palette preview   http://localhost:$TT_DESKTOP_PORT  (needs Node)
  slack    Slack bot (needs SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET, TT_SLACK_LINK_SECRET)
  Default: server worker web. Each start waits for the health check and refuses a port that is already taken.

OPTIONS
  -f, --foreground   run one component attached to the terminal
  Settings: TT_HOST TT_PORT TT_WEB_PORT TT_GATEWAY_PORT TT_RELOAD TT_START_TIMEOUT TT_WEB_MODE=vite|static

FILES
  .run/<component>.pid    .run/logs/<component>.log

EXAMPLES
  $TT_TTCTL start
  $TT_TTCTL start server worker
  $TT_TTCTL -v start gateway
  TT_PORT=9000 $TT_TTCTL start server
  $TT_TTCTL run server

SEE ALSO
  $GUIDE/04-run-and-stop.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1

FOREGROUND=0; NAMES=()
for a in "$@"; do case "$a" in -f|--foreground) FOREGROUND=1 ;; -*) usage_fail "unknown option '$a' for start" ;; *) NAMES+=("$a") ;; esac; done
resolve_components "${NAMES[@]+"${NAMES[@]}"}"
[[ $FOREGROUND -eq 1 && ${#COMPONENTS[@]} -ne 1 ]] && usage_fail "run / --foreground takes exactly one component" "you named ${#COMPONENTS[@]}"
need_python_env
run mkdir -p "$LOG_DIR"

web_mode() {   # vite when Node + node_modules are here, else the prebuilt static build
  local m="${TT_WEB_MODE:-}"
  if [[ -z "$m" ]]; then if have node && [[ -d node_modules ]]; then m=vite; else m=static; fi; fi
  echo "$m"
}
component_cmd() {
  case "$1" in
    server)
      local reload=""; [[ "$TT_RELOAD" == "1" && "${TT_OFFLINE:-0}" != 1 ]] && reload="--reload --reload-dir apps --reload-dir packages"
      echo "${PY_RUN[*]} uvicorn tt_server.main:create_app --factory --host $TT_HOST --port $TT_PORT $reload" ;;
    worker) echo "${PY_RUN[*]} python -m tt_worker" ;;
    web)
      if [[ "$(web_mode)" == vite ]]; then echo "pnpm --filter @tt/web dev --port $TT_WEB_PORT --strictPort"
      else echo "$ROOT/.venv/bin/python $ROOT/scripts/lib/webserve.py --dist $ROOT/apps/web/dist --port $TT_WEB_PORT --api $TT_API_URL"; fi ;;
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
      if have pg_isready && ! probe pg_isready -q -h "$h" -p "$p" >/dev/null; then
        fail TT-E200 "$1 needs the database, and nothing answers on $h:$p" "PostgreSQL is not running (or TT_DATABASE_URL points elsewhere)" \
          "$(tt_cmd db start)   (new install: $(tt_cmd db setup))"
      fi
      # reachable but never migrated: the server would start and answer 503 on /readyz
      local j; j="$(probe "$ROOT/.venv/bin/python" "$ROOT/scripts/lib/dbcheck.py" || true)"
      if [[ "$j" == *'"reachable": true'* && "$j" == *'"revision": null'* ]]; then
        fail TT-E204 "$1 needs the database schema, and the database has never been migrated" "the alembic_version table is missing in $(db_part name)" \
          "$(tt_cmd db migrate)   (new install: $(tt_cmd db setup))"
      fi ;;
    web)
      if [[ "$(web_mode)" == vite ]]; then
        [[ -f apps/presentation/dist/presentation.html ]] || step "build the deck viewer" pnpm --filter @tt/presentation build
      else
        [[ -f apps/web/dist/index.html ]] || fail TT-E030 "the web console is not built and Node is not available to run it" \
          "apps/web/dist/index.html is missing and node/node_modules are not installed" "$(tt_cmd setup)  (installs Node and builds), or $(tt_cmd build web)" \
          "install a bundle: it ships apps/web/dist prebuilt"
      fi ;;
    desktop) have node && [[ -d node_modules ]] || fail TT-E030 "the desktop preview needs Node and node_modules" "they are not installed here" \
               "$(tt_cmd setup)" "build the bundle with --with-dev to bring Node and the pnpm store" ;;
    slack)
      local v; for v in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_SIGNING_SECRET TT_SLACK_LINK_SECRET; do
        [[ -n "${!v:-}" ]] || fail TT-E304 "the Slack bot needs $v" "it is not set in the environment or .env" "add $v=... to .env (see $GUIDE/08-clients.md#slack-bot)"
      done
      export TT_SERVER_URL="${TT_SERVER_URL:-$TT_API_URL}" ;;
  esac
}

start_one() {
  local c="$1" pid port cmd url log
  if pid="$(running_pid "$c")"; then ok "$c already running (pid $pid)"; return 0; fi
  port="$(component_port "$c")"
  if [[ -n "$port" ]] && port_in_use "$port"; then
    fail TT-E300 "$c cannot start: port $port is already in use" "another program listens on $port (STATE 'external' in $(tt_cmd status))" \
      "stop that program (find it: ss -ltnp | grep :$port), or choose another port, e.g. TT_PORT / TT_WEB_PORT in .env"
  fi
  preflight "$c"
  cmd="$(component_cmd "$c")"; log="$(log_file "$c")"
  detail "$c command: $cmd"
  if [[ $FOREGROUND -eq 1 ]]; then info "$c (foreground): $cmd"; dry_run && return 0; exec bash -c "exec $cmd"; fi
  if dry_run; then say "would start $c: $cmd"; return 0; fi
  printf '\n=== %s start: %s\n' "$(date -Is)" "$cmd" >> "$log"
  setsid bash -c "exec $cmd" >> "$log" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$(pid_file "$c")"
  url="$(component_url "$c")"
  if [[ -n "$url" ]]; then
    if wait_http "$url" "${TT_START_TIMEOUT:-90}" "$pid"; then ok "$c started (pid $pid) - ${url%/readyz}"
    else
      tail -n 25 "$log" >&2 || true
      kill_tree "$pid" 5; rm -f "$(pid_file "$c")"
      fail TT-E301 "$c did not become healthy at $url within ${TT_START_TIMEOUT:-90}s" "the last 25 lines of ${log#"$ROOT"/} are above" \
        "fix the cause in the log ($(tt_cmd logs "$c")), then $(tt_cmd start "$c")"
    fi
  else
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then ok "$c started (pid $pid) - log: ${log#"$ROOT"/}"
    else
      tail -n 25 "$log" >&2 || true; rm -f "$(pid_file "$c")"
      fail TT-E301 "$c exited right after starting" "the last 25 lines of ${log#"$ROOT"/} are above" "fix the cause in the log, then $(tt_cmd start "$c")"
    fi
  fi
}

for c in "${COMPONENTS[@]}"; do start_one "$c"; done
if [[ " ${COMPONENTS[*]} " == *" web "* ]] && ! dry_run; then
  say ""; say "Open http://localhost:$TT_WEB_PORT/login$( [[ "${TT_DEV_AUTH:-0}" =~ ^(1|true)$ ]] && echo "   (Development sign-in: workspace acme, handle @mahmoud)")"
fi
if [[ " ${COMPONENTS[*]} " == *" gateway "* ]] && ! dry_run; then
  say "LLM gateway runs in privacy_mode=local_only. With no local model installed, the app keeps using the deterministic parser."
fi
