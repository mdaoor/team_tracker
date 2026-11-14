#!/usr/bin/env bash
# Remove generated files.
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl clean - remove build output, caches and logs (never .env, the database, backups/ or .tools/)

SYNOPSIS
  $TT_TTCTL clean [--deps] [--tools]

OPTIONS
  --deps    also .venv and node_modules (run setup again afterwards)
  --tools   also .tools/ (the user-space prerequisites)

EXAMPLES
  $TT_TTCTL clean
  $TT_TTCTL --dry-run clean --deps
  $TT_TTCTL clean --deps --tools

SEE ALSO
  $GUIDE/12-uninstall.md
EOF2
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
DEPS=0; TOOLS=0
for a in "$@"; do case "$a" in --deps) DEPS=1 ;; --tools) TOOLS=1 ;; *) usage_fail "unknown option '$a' for clean" ;; esac; done
for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do
  running_pid "$c" >/dev/null && fail TT-E303 "$c is running" "clean would delete files it uses" "$(tt_cmd stop), then $(tt_cmd clean)"
done
# a bundle install ships apps/*/dist prebuilt; keep them when there is no Node to rebuild them
dists="apps/web/dist apps/presentation/dist apps/desktop/dist"; [[ -f .ttctl.env ]] && ! have node && dists=""
run rm -rf $dists dist/client .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage apps/web/playwright-report apps/web/test-results apps/presentation/test-results
run find "$LOG_DIR" -name '*.log' ! -path "$TT_LOG_FILE" -delete 2>/dev/null || true
run find . -name __pycache__ -type d -not -path './node_modules/*' -not -path './.venv/*' -not -path './.tools/*' -prune -exec rm -rf {} +
ok "build outputs, caches and logs removed"
if [[ $DEPS -eq 1 ]]; then run rm -rf .venv node_modules apps/*/node_modules packages/*/node_modules; ok "dependencies removed - $(tt_cmd setup) reinstalls them"; fi
if [[ $TOOLS -eq 1 ]]; then run rm -rf .tools; ok ".tools removed - $(tt_cmd prereqs install) reinstalls them"; fi
