#!/usr/bin/env bash
# Remove generated files. Never touches .env, the database or backups/.
#
#   scripts/clean.sh             build outputs, caches, logs (.run/logs)
#   scripts/clean.sh --deps      ...and .venv + node_modules (run scripts/setup.sh again afterwards)
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1
DEPS=0; [[ "${1:-}" == "--deps" ]] && DEPS=1
[[ "${1:-}" =~ ^(-h|--help)$ ]] && { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

for c in "${ALL_COMPONENTS[@]}" "${OPTIONAL_COMPONENTS[@]}"; do
  running_pid "$c" >/dev/null && die "$c is running - scripts/ttctl stop first"
done
rm -rf apps/web/dist apps/presentation/dist apps/desktop/dist dist/client \
       .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage .coverage.* \
       apps/web/playwright-report apps/web/test-results apps/presentation/test-results "$LOG_DIR"
find . -name __pycache__ -type d -not -path './node_modules/*' -not -path './.venv/*' -prune -exec rm -rf {} +
ok "build outputs, caches and logs removed"
if [[ $DEPS -eq 1 ]]; then
  rm -rf .venv node_modules apps/*/node_modules packages/*/node_modules
  ok "dependencies removed - run scripts/setup.sh to reinstall"
fi
