#!/usr/bin/env bash
# Detect and install the prerequisites.
source "$(dirname "$0")/lib/common.sh"
source "$ROOT/scripts/lib/prereqs.sh"

usage() {
  cat <<EOF
ttctl prereqs - detect and install what Team Tracker needs

SYNOPSIS
  $TT_TTCTL prereqs check [--json]
  $TT_TTCTL prereqs install [TOOL...] [--user|--system] [--from-bundle DIR]

TOOLS
  user space (no root, into .tools/):  uv  python ($TT_PYTHON_VERSION, uv-managed)  node ($TT_NODE_VERSION)  pnpm ($TT_PNPM_VERSION)  chromium
  system packages (root):              postgresql ($TT_PG_MAJOR server + contrib)  psql ($TT_PG_MAJOR client)  curl  git  chromium-libs  docker
  Without TOOL, "install" installs every REQUIRED tool that is missing or too old.

OPTIONS
  --json              check: machine-readable output
  --user              install only user-space tools; print the commands for system packages instead of running them
  --system            also install system packages: shows the exact commands, asks first (or --yes), uses sudo when not root
  --from-bundle DIR   take the tools from an unpacked air-gapped bundle instead of the network
  TT_PG_SOURCE=module|pgdg   RHEL: PostgreSQL from the AppStream module stream postgresql:$TT_PG_MAJOR or from the PGDG repository
                             (default: the module when the host offers it)

SUPPORTED
  RHEL / Rocky / Alma 8 and 9, Ubuntu 22.04 and 24.04, Debian 12 (x86_64); macOS for development only.

EXAMPLES
  $TT_TTCTL prereqs check
  $TT_TTCTL -v prereqs check --json
  $TT_TTCTL prereqs install pnpm
  $TT_TTCTL prereqs install postgresql --system --dry-run
  $TT_TTCTL prereqs install --from-bundle /opt/tt-bundle

SEE ALSO
  $GUIDE/01-requirements.md   $GUIDE/02-install-online.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"

sub="${1:-check}"; shift || true
JSON=0; SCOPE=--auto; FROM=""; TOOLS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1 ;; --user) SCOPE=--user ;; --system) SCOPE=--system ;;
    --from-bundle) [[ $# -ge 2 ]] || usage_fail "--from-bundle needs a directory"; FROM="$(cd "$2" && pwd)"; shift ;;
    -*) usage_fail "unknown option '$1' for prereqs $sub" ;;
    *) TOOLS+=("$1") ;;
  esac; shift
done

# Required for a source install. Node/pnpm are optional when the web console is prebuilt (bundle installs).
required_tool() { case "$1" in python|uv|node|pnpm|curl) return 0 ;; *) return 1 ;; esac; }

check() {
  local t rows=() missing=() sep=""
  detail "os: $(tt_os_id) $(tt_arch) (supported: $(tt_os_supported && echo yes || echo no))"
  for t in "${PREREQ_TOOLS[@]}"; do
    tool_probe "$t"
    rows+=("$t|$P_STATUS|${P_VERSION:--}|$P_NEED|${P_PATH:--}|$(tool_kind "$t")|$P_NOTE")
    if required_tool "$t" && [[ "$P_STATUS" == missing || "$P_STATUS" == old ]]; then missing+=("$t"); fi
  done
  if [[ $JSON -eq 1 ]]; then
    printf '{"os":"%s","arch":"%s","supported":%s,"offline":%s,"tools":[' "$(tt_os_id)" "$(tt_arch)" \
      "$(tt_os_supported && echo true || echo false)" "$([[ "${TT_OFFLINE:-0}" == 1 ]] && echo true || echo false)"
    for r in "${rows[@]}"; do
      IFS='|' read -r t s v n p k note <<<"$r"
      printf '%s{"tool":"%s","status":"%s","version":"%s","need":"%s","path":"%s","install":"%s","note":"%s"}' "$sep" "$t" "$s" "$v" "$n" "$p" "$k" "${note//\"/\'}"
      sep=","
    done
    local m="" x; for x in "${missing[@]+"${missing[@]}"}"; do m+="${m:+,}\"$x\""; done
    printf '],"missing_required":[%s]}\n' "$m"
  else
    say "$(tt_os_id) $(tt_arch)$(tt_os_supported || echo '  (not a supported OS - the installer will only print commands)')"
    printf '%-11s %-17s %-10s %-26s %s\n' TOOL STATUS VERSION NEEDS NOTE
    for r in "${rows[@]}"; do
      IFS='|' read -r t s v n p k note <<<"$r"
      case "$s" in ok) c="$C_GRN" ;; optional-missing) c="$C_DIM" ;; *) c="$C_RED" ;; esac
      required_tool "$t" || [[ "$s" == ok ]] || c="$C_YLW"
      printf '%-11s %s%-17s%s %-10s %-26s %s\n' "$t" "$c" "$s" "$C_OFF" "$v" "${n:0:26}" "$note"
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
      say ""; say "Missing or too old: ${missing[*]}"
      if [[ "${TT_OFFLINE:-0}" == 1 ]]; then say "Install from the bundle: $(tt_cmd prereqs install --from-bundle DIR)"
      else say "Install them (user space, no root): $(tt_cmd prereqs install "${missing[@]}")"; fi
    fi
  fi
  [[ ${#missing[@]} -eq 0 ]] || return $E_PREREQ
}

install() {
  local t list=()
  if [[ ${#TOOLS[@]} -eq 0 ]]; then
    for t in "${PREREQ_TOOLS[@]}"; do
      required_tool "$t" || continue
      tool_probe "$t"; [[ "$P_STATUS" == missing || "$P_STATUS" == old ]] && list+=("$t")
    done
    [[ ${#list[@]} -gt 0 ]] || { ok "every required tool is present"; return 0; }
  else
    list=("${TOOLS[@]}")
  fi
  for t in "${list[@]}"; do
    info "installing $t$([[ -n "$FROM" ]] && echo " from $FROM")"
    install_tool "$t" "$SCOPE" "$FROM" || true
    hash -r
    if ! dry_run && [[ "$t" != chromium-libs && "$t" != docker ]]; then
      tool_probe "$t"
      case "$P_STATUS" in
        ok) ok "$t ${P_VERSION} ($P_PATH)" ;;
        optional-missing) ok "$t: $P_NOTE" ;;
        *) [[ "$(tool_kind "$t")" == system && "$SCOPE" != --system ]] || \
           fail TT-E120 "$t is still $P_STATUS after installing" "${P_NOTE:-the install reported success but the tool is not usable}" "re-run with -v: $(tt_cmd -v prereqs install "$t")" ;;
      esac
    fi
  done
  [[ -d "$TOOLS_DIR/bin" ]] && say "Tools in ${TOOLS_DIR#"$ROOT"/} are used automatically by $TT_TTCTL. For your own shell: export PATH=\"$TOOLS_DIR/bin:$TOOLS_DIR/node/bin:\$PATH\""
  return 0
}

case "$sub" in
  check) check || exit $? ;;
  install) install ;;
  *) fail TT-E011 "unknown prereqs subcommand '$sub'" "subcommands: check, install" "$TT_TTCTL help prereqs" ;;
esac
