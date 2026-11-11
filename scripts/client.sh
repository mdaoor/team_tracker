#!/usr/bin/env bash
# Reporter client ("tt" command line) - install it, sign in, use it.
#
#   scripts/client.sh install                 install "tt" on this machine as a user tool (~/.local/bin/tt) with uv
#   scripts/client.sh uninstall               remove it
#   scripts/client.sh wheels [DIR]            build installable wheels (tt-cli + its two libraries) into DIR (default dist/client)
#                                             to hand to people who do not have the repository:
#                                             uv tool install --find-links DIR tt-cli
#   scripts/client.sh token [@handle] [ws]    DEV ONLY: mint an API token for @handle (default @mahmoud) in workspace (default acme)
#   scripts/client.sh login [@handle] [ws]    DEV ONLY: mint a token and run "tt login" against the local server
#   scripts/client.sh run ARGS...             run tt from the repository without installing it (same as: uv run tt ARGS)
#   scripts/client.sh logout                  delete the stored client configuration
#
# Real users sign in with:  tt login --url https://tracker.example.com --workspace acme --browser
#                       or: tt login --url ... --workspace acme --ldap-user 'CORP\\jdoe'
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1
CMD="${1:-help}"; shift || true

mint_token() {
  local handle="${1:-@mahmoud}" ws="${2:-acme}"
  [[ "${TT_DEV_AUTH:-0}" =~ ^(1|true)$ ]] || die "token/login here use the Development sign-in (TT_DEV_AUTH=1). On a real server create the token in the web console: Profile -> API tokens"
  http_ok "$TT_API_URL/readyz" || die "the API is not answering on $TT_API_URL - scripts/ttctl start server"
  curl -fsS -X POST "$TT_API_URL/auth/tokens" -H "X-Debug-Actor: $handle" -H 'content-type: application/json' \
    -d "{\"workspace\":\"$ws\",\"name\":\"cli-$(hostname -s 2>/dev/null || echo local)\",\"scopes\":[\"capture\"]}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'
}

case "$CMD" in
  install)
    need uv "curl -LsSf https://astral.sh/uv/install.sh | sh"
    uv tool install --force --python 3.12 --from ./apps/cli tt-cli --with ./packages/client_core --with ./packages/schema
    bin="$(uv tool dir --bin)"; ok "installed: $bin/tt"
    [[ ":$PATH:" == *":$bin:"* ]] || warn "$bin is not on your PATH - run: uv tool update-shell   (then open a new terminal)"
    echo "Next: tt login --url <server> --workspace <slug> --browser   (dev: scripts/client.sh login)" ;;
  uninstall) uv tool uninstall tt-cli && ok "tt removed" ;;
  wheels)
    out="${1:-$ROOT/dist/client}"; mkdir -p "$out"
    for p in packages/schema packages/client_core apps/cli; do uv build --wheel --out-dir "$out" "$p"; done
    ok "wheels in $out:"; ls -1 "$out"
    echo "Install elsewhere with: uv tool install --find-links $out tt-cli" ;;
  token) mint_token "$@" ;;
  login)
    handle="${1:-@mahmoud}"; ws="${2:-acme}"; tok="$(mint_token "$handle" "$ws")"
    tz="${TT_TIME_ZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || true)}"; tz="${tz:-UTC}"
    "${PY_RUN[@]}" tt login --url "$TT_API_URL" --workspace "$ws" --handle "$handle" --token "$tok" \
      --time-zone "$tz"
    ok "signed in as $handle in $ws - try: scripts/client.sh run say \"fixed the login bug, took 2h\" --yes" ;;
  run) exec "${PY_RUN[@]}" tt "$@" ;;
  logout)
    cfg="${TT_CONFIG_DIR:-$HOME/.config/tt}/config.toml"
    if [[ -f "$cfg" ]]; then rm -f "$cfg"; ok "removed $cfg (the outbox in ${TT_DATA_DIR:-$HOME/.local/share/tt} is kept)"; else warn "not signed in ($cfg not found)"; fi ;;
  -h|--help|help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown client command '$CMD' (see scripts/client.sh --help)" ;;
esac
