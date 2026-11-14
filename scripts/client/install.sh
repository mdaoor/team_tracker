#!/usr/bin/env bash
# Team Tracker client kit - install the "tt" command line on Linux or macOS, online or offline.
#
#   ./install.sh install [--server URL] [--workspace SLUG] [--ca FILE]   install tt (offline from ./wheelhouse when present)
#   ./install.sh login [--browser|--token TOKEN --handle @me|--ldap-user 'DOMAIN\user']
#   ./install.sh status | uninstall
#   options: -v/--verbose  --offline (never use the network; default when ./wheelhouse exists)  -y/--yes  -h/--help
#   exit codes: 0 ok, 1 failure, 2 usage, 3 prerequisite missing (Python 3.12 / uv), 4 offline and something is missing
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VERBOSE=0; OFFLINE=0; [[ -d "$HERE/wheelhouse" ]] && OFFLINE=1
CMD=""; SERVER=""; WS=""; CA=""; LOGIN_ARGS=()
die() { local code="$1" rc="$2"; shift 2; printf 'error %s\n  WHAT  %s\n  WHY   %s\n  FIX   %s\n  DOCS  docs/guide/08-clients.md#client-kit\n' "$code" "$1" "$2" "$3" >&2; exit "$rc"; }
v() { [[ $VERBOSE -eq 1 ]] && printf '  · %s\n' "$*" >&2; return 0; }
usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    -v|--verbose) VERBOSE=1 ;; --offline) OFFLINE=1 ;; --online) OFFLINE=0 ;; -y|--yes) ;; -h|--help) usage; exit 0 ;;
    --server) SERVER="$2"; shift ;; --workspace) WS="$2"; shift ;; --ca) CA="$2"; shift ;;
    install|login|status|uninstall) CMD="$1" ;;
    *) if [[ "$CMD" == login ]]; then LOGIN_ARGS+=("$1"); else die TT-E010 2 "unknown argument '$1'" "see ./install.sh --help" "./install.sh --help"; fi ;;
  esac; shift
done
[[ -n "$CMD" ]] || { usage; exit 2; }
CONF="$HOME/.config/tt"; mkdir -p "$CONF"
find_uv() {
  for c in "$(command -v uv 2>/dev/null || true)" "$HOME/.local/bin/uv" "$HERE/bin/uv"; do [[ -n "$c" && -x "$c" ]] && { echo "$c"; return 0; }; done
  return 1
}
case "$CMD" in
  install)
    if UV="$(find_uv)"; then
      v "uv: $UV"
      if [[ $OFFLINE -eq 1 ]]; then
        [[ -d "$HERE/wheelhouse" ]] || die TT-E401 4 "offline install without a wheelhouse" "this kit has no wheelhouse/ folder" "use the client kit from a bundle (client-kit/), or --online"
        "$UV" tool install --force --offline --no-index --find-links "$HERE/wheelhouse" tt-cli
      else "$UV" tool install --force tt-cli --find-links "$HERE/wheelhouse"; fi
    else
      PY="$(command -v python3.12 || command -v python3.13 || command -v python3 || true)"
      [[ -n "$PY" ]] && "$PY" -c 'import sys;sys.exit(sys.version_info<(3,12))' || \
        die TT-E100 3 "Python 3.12+ or uv is needed" "neither uv nor a Python 3.12+ was found" "install Python 3.12 (python.org / your OS packages), then ./install.sh install"
      "$PY" -m venv "$HOME/.local/share/tt/venv"
      pipargs=(); [[ $OFFLINE -eq 1 ]] && pipargs=(--no-index --find-links "$HERE/wheelhouse")
      "$HOME/.local/share/tt/venv/bin/pip" install -q "${pipargs[@]}" tt-cli
      mkdir -p "$HOME/.local/bin"; ln -sf "$HOME/.local/share/tt/venv/bin/tt" "$HOME/.local/bin/tt"
    fi
    if [[ -n "$CA" || -f "$HERE/ca/ca.pem" ]]; then cp "${CA:-$HERE/ca/ca.pem}" "$CONF/ca.pem"; echo "CA bundle: $CONF/ca.pem"; fi
    [[ -n "$SERVER" ]] && printf 'server_url = "%s"\nworkspace = "%s"\n' "$SERVER" "$WS" > "$CONF/kit-defaults.toml"
    echo "installed: $(command -v tt || echo "$HOME/.local/bin/tt")"
    echo "next: ./install.sh login --browser   (or: tt login --url ${SERVER:-https://tracker.example.com} --workspace ${WS:-acme} --browser)" ;;
  login)
    command -v tt >/dev/null || die TT-E100 3 "tt is not installed" "run install first" "./install.sh install"
    if [[ -z "$SERVER" && -f "$CONF/kit-defaults.toml" ]]; then SERVER="$(sed -n 's/^server_url = "\(.*\)"/\1/p' "$CONF/kit-defaults.toml")"; WS="$(sed -n 's/^workspace = "\(.*\)"/\1/p' "$CONF/kit-defaults.toml")"; fi
    [[ -n "$SERVER" && -n "$WS" ]] || die TT-E012 2 "login needs the server URL and workspace" "no --server/--workspace given and none saved by install" "./install.sh login --server https://tracker.example.com --workspace acme --browser"
    ca=(); [[ -f "$CONF/ca.pem" ]] && ca=(--ca-bundle "$CONF/ca.pem")
    tt login --url "$SERVER" --workspace "$WS" "${ca[@]}" "${LOGIN_ARGS[@]+"${LOGIN_ARGS[@]}"}" ;;
  status)
    command -v tt >/dev/null && echo "tt: $(command -v tt)" || echo "tt: not installed"
    [[ -f "$CONF/config.toml" ]] && grep -E '^(server_url|workspace|handle) ' "$CONF/config.toml" || echo "not signed in" ;;
  uninstall)
    if UV="$(find_uv)" && "$UV" tool list 2>/dev/null | grep -q '^tt-cli'; then "$UV" tool uninstall tt-cli; fi
    rm -rf "$HOME/.local/share/tt/venv"; rm -f "$HOME/.local/bin/tt"
    echo "tt removed (your sign-in in $CONF and outbox in ~/.local/share/tt are kept; delete them to remove everything)" ;;
esac
