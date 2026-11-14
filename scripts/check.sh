#!/usr/bin/env bash
# Quality checks (what CI runs).
source "$(dirname "$0")/lib/common.sh"
usage() {
  cat <<EOF2
ttctl check - lint, type checks, tests

SYNOPSIS
  $TT_TTCTL check [all|lint|typecheck|test [PYTEST ARGS]|quick|e2e|fmt|ops]

SUBCOMMANDS
  all         lint + typecheck + test (= make check)
  lint        ruff + eslint/prettier          typecheck   mypy + tsc
  test        pytest (extra args go to pytest) + vitest
  quick       Python unit + package tests and web unit tests
  e2e         Playwright (needs Chromium)     fmt         auto-format
  ops         the ops tests: shellcheck, bats (scripts/lib), --help for every command, docs consistency

  Python tests need an empty PostgreSQL 16 database in TT_TEST_DATABASE_URL, or Docker for a testcontainer.

EXAMPLES
  $TT_TTCTL check quick
  $TT_TTCTL check test -k capture -x
  $TT_TTCTL check ops

SEE ALSO
  $GUIDE/10-scripts-reference.md
EOF2
}
TT_PASSTHROUGH_WORDS="test quick"
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
CMD="${1:-all}"; shift || true
case "$CMD" in
  all) step "lint" make lint; step "typecheck" make typecheck; step "test" make test ;;
  lint) step "lint" make lint ;;
  typecheck) step "typecheck" make typecheck ;;
  test) run "${PY_RUN[@]}" pytest -q "$@"; run pnpm --filter @tt/web test; run pnpm --filter @tt/desktop test ;;
  quick)
    run "${PY_RUN[@]}" pytest -q -x apps/server/tests/unit packages/client_core/tests packages/schema/tests packages/filter_grammar/tests packages/cel_policies/tests "$@"
    run pnpm --filter @tt/web test ;;
  e2e) step "e2e" make e2e ;;
  fmt) run make fmt ;;
  ops) run "$ROOT/tests/ops/run.sh" "$@" ;;
  *) fail TT-E011 "unknown check '$CMD'" "checks: all lint typecheck test quick e2e fmt ops" "$TT_TTCTL help check" ;;
esac
