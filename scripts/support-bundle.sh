#!/usr/bin/env bash
# One tar with what support needs, safe to carry out of a closed site.
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF
ttctl support-bundle - logs, doctor output, versions and the MASKED configuration in one file

SYNOPSIS
  $TT_TTCTL support-bundle [FILE] [--days N]

CONTENTS
  versions.txt (ttctl version), doctor.txt, prereqs.json, status.json, config.txt (TT_* settings with secrets and
  database passwords replaced by ***), the component and ttctl run logs of the last N days (default 3),
  bundle manifest.json when installed from a bundle, systemd unit status and the last 500 journal lines in production.
  Not included: .env itself, the database, uploaded files, anything under /etc/tt except unit status.
  Review the file before sending it: logs can contain work item titles.

OPTIONS
  FILE       output (default .run/tt-support-<host>-<time>.tar.gz)
  --days N   how many days of logs (default 3)

EXAMPLES
  $TT_TTCTL support-bundle
  $TT_TTCTL support-bundle /media/usb/tt-support.tar.gz
  $TT_TTCTL support-bundle --days 14

SEE ALSO
  $GUIDE/11-troubleshooting.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
OUT=""; DAYS=3
while [[ $# -gt 0 ]]; do
  case "$1" in --days) [[ "${2:-}" =~ ^[0-9]+$ ]] || usage_fail "--days needs a number"; DAYS="$2"; shift ;; -*) usage_fail "unknown option '$1'" ;; *) OUT="$1" ;; esac; shift
done
[[ -n "$OUT" ]] || OUT="$RUN_DIR/tt-support-$(uname -n)-$(date +%Y%m%d-%H%M%S).tar.gz"
tmp="$(mktemp -d)"; d="$tmp/tt-support"; mkdir -p "$d/logs"
say "collecting into $OUT"
"$ROOT/scripts/ttctl" version > "$d/versions.txt" 2>&1 || true
NO_COLOR=1 "$ROOT/scripts/doctor.sh" > "$d/doctor.txt" 2>&1 || true
"$ROOT/scripts/prereqs.sh" check --json > "$d/prereqs.json" 2>/dev/null || true
"$ROOT/scripts/status.sh" --json > "$d/status.json" 2>/dev/null || true
{
  echo "# effective configuration, secrets masked ($(date -Is))"
  load_env
  for k in $(compgen -e | grep -E '^(TT_|UV_|PIP_|npm_config_|PLAYWRIGHT_|COREPACK_)' | sort); do echo "$k=$(tt_mask "$k" "${!k}")"; done
} > "$d/config.txt"
find "$LOG_DIR" -maxdepth 1 -name '*.log' -mtime "-$DAYS" -exec cp {} "$d/logs/" \; 2>/dev/null || true
# mask anything that looks like a secret inside the copied logs too
find "$d/logs" -type f -exec sed -i -E 's#(postgresql(\+psycopg)?://[^:/@ ]+:)[^@ ]+@#\1***@#g; s#((SECRET|PASSWORD|TOKEN|_KEY)[A-Z_]*=)[^ ]+#\1***#g; s#(Bearer|token=)[ ]?[A-Za-z0-9._~+/=-]{8,}#\1 ***#g' {} +
[[ -n "${TT_BUNDLE_DIR:-}" && -f "${TT_BUNDLE_DIR:-}/manifest.json" ]] && cp "$TT_BUNDLE_DIR/manifest.json" "$d/bundle-manifest.json"
if have systemctl && systemctl cat tt-server >/dev/null 2>&1; then
  systemctl status tt-server tt-worker tt-llm-gateway --no-pager > "$d/systemd-status.txt" 2>&1 || true
  journalctl -u tt-server -u tt-worker -u tt-llm-gateway -n 500 --no-pager 2>/dev/null \
    | sed -E 's#(postgresql(\+psycopg)?://[^:/@ ]+:)[^@ ]+@#\1***@#g' > "$d/journal.txt" || true
fi
run mkdir -p "$(dirname "$OUT")"
run tar -czf "$OUT" -C "$tmp" tt-support
rm -rf "$tmp"
dry_run || ok "support bundle: $OUT ($(du -h "$OUT" | cut -f1)) - review it before sending (logs can contain work item titles)"
