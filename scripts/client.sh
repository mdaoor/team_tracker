#!/usr/bin/env bash
# Reporter client ("tt" command line): install it, sign in, use it.
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl client - install and use the "tt" command line

SYNOPSIS
  $TT_TTCTL client install | uninstall | status
  $TT_TTCTL client wheels [DIR]               installable wheels (tt-cli + its two libraries), default dist/client
  $TT_TTCTL client token [@handle] [ws]       DEV ONLY: mint an API token (Development sign-in)
  $TT_TTCTL client login [@handle] [ws]       DEV ONLY: mint a token and run "tt login" against this server
  $TT_TTCTL client run ARGS...                run tt from the repository without installing it
  $TT_TTCTL client logout                     delete the stored client configuration (keeps the outbox)

  On a user's machine without this repository (or offline) use the client kit instead:
    Linux/macOS:  ./install.sh install        Windows:  .\\install.ps1 install      ($GUIDE/08-clients.md)

EXAMPLES
  $TT_TTCTL client install
  $TT_TTCTL client login @sara
  $TT_TTCTL client run say "fixed the login loop, took 2h" --yes
  $TT_TTCTL client wheels dist/client

SEE ALSO
  $GUIDE/08-clients.md
EOF2
}
# shellcheck disable=SC2034  # read by tt_init in lib/common.sh
TT_PASSTHROUGH_WORDS="run"
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
CMD="${1:-help}"; shift || true

mint_token() {
  local handle="${1:-@mahmoud}" ws="${2:-acme}"
  [[ "${TT_DEV_AUTH:-0}" =~ ^(1|true)$ ]] || fail TT-E305 "minting a token here needs the Development sign-in" "TT_DEV_AUTH is off on this server" \
    "on a real server: web console -> Profile -> API tokens, then: tt login --url URL --workspace $ws --handle $handle --token TOKEN"
  http_ok "$TT_API_URL/readyz" || fail TT-E306 "the API is not answering on $TT_API_URL" "the server is not running" "$(tt_cmd start server)"
  curl -fsS -X POST "$TT_API_URL/auth/tokens" -H "X-Debug-Actor: $handle" -H 'content-type: application/json' \
    -d "{\"workspace\":\"$ws\",\"name\":\"cli-$(uname -n)\",\"scopes\":[\"capture\"]}" \
    | "$(tt_python)" -c 'import sys,json;print(json.load(sys.stdin)["token"])'
}
cfg="${TT_CONFIG_DIR:-$HOME/.config/tt}/config.toml"

case "$CMD" in
  install)
    have uv || fail TT-E100 "installing tt needs uv" "uv is not on PATH or in .tools/bin" "$(tt_cmd prereqs install uv)"
    if [[ "${TT_OFFLINE:-0}" == 1 ]]; then
      [[ -d "$ROOT/wheelhouse" ]] || fail TT-E401 "no wheelhouse to install tt from" "this offline install has no wheelhouse/" "$(tt_cmd bundle install FILE)"
      run uv tool install --force --offline --no-index --find-links "$ROOT/wheelhouse" tt-cli
    else
      run uv tool install --force --python 3.12 --from ./apps/cli tt-cli --with ./packages/client_core --with ./packages/schema
    fi
    bin="$(uv tool dir --bin)"; ok "installed: $bin/tt"
    [[ ":$PATH:" == *":$bin:"* ]] || warn "$bin is not on your PATH - run: uv tool update-shell   (then open a new terminal)"
    say "Next: tt login --url <server> --workspace <slug> --browser   (dev: $(tt_cmd client login))" ;;
  uninstall) run uv tool uninstall tt-cli; ok "tt removed" ;;
  status)
    if have tt; then ok "tt installed: $(command -v tt)"; else warn "tt is not installed ($(tt_cmd client install))"; fi
    if [[ -f "$cfg" ]]; then ok "signed in: $(grep -E '^(server_url|workspace|handle) ' "$cfg" | tr '\n' ' ' | sed 's/ = /=/g')"; else warn "not signed in ($cfg missing)"; fi ;;
  wheels)
    out="${1:-$ROOT/dist/client}"; run mkdir -p "$out"
    for p in packages/schema packages/client_core apps/cli; do step "build $p" uv build --wheel --out-dir "$out" "$p"; done
    ok "wheels in $out"; say "Install elsewhere with: uv tool install --find-links $out tt-cli" ;;
  token) mint_token "$@" ;;
  login)
    handle="${1:-@mahmoud}"; ws="${2:-acme}"; tok="$(mint_token "$handle" "$ws")"
    tz="${TT_TIME_ZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || true)}"; tz="${tz:-UTC}"
    run "${PY_RUN[@]}" tt login --url "$TT_API_URL" --workspace "$ws" --handle "$handle" --token "$tok" --time-zone "$tz"
    ok "signed in as $handle in $ws - try: $(tt_cmd client run say "\"fixed the login bug, took 2h\"" --yes)" ;;
  run) need_python_env; exec "${PY_RUN[@]}" tt "$@" ;;
  logout)
    if [[ -f "$cfg" ]]; then run rm -f "$cfg"; ok "removed $cfg (the outbox in ${TT_DATA_DIR:-$HOME/.local/share/tt} is kept)"; else warn "not signed in ($cfg not found)"; fi ;;
  help) usage ;;
  *) fail TT-E011 "unknown client command '$CMD'" "commands: install uninstall status wheels token login run logout" "$TT_TTCTL help client" ;;
esac
