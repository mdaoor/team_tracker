#!/usr/bin/env bash
# Quality checks (what CI runs).
#
#   scripts/check.sh                 lint + typecheck + tests         (same as make check)
#   scripts/check.sh lint            ruff + eslint/prettier
#   scripts/check.sh typecheck       mypy + tsc
#   scripts/check.sh test [ARGS]     pytest (ARGS are passed to pytest, e.g. -k capture -x) + vitest
#   scripts/check.sh quick           fast subset: Python unit tests + web unit tests
#   scripts/check.sh e2e             Playwright (needs Chromium: scripts/setup.sh --browsers)
#   scripts/check.sh fmt             auto-format Python and web code
#
# The Python tests need an empty Postgres 16 database. Set TT_TEST_DATABASE_URL
# (e.g. postgresql+psycopg://tt:tt@localhost:5432/tt_test, create it with: createdb -O tt tt_test), otherwise a testcontainer is started.
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT" || exit 1
CMD="${1:-all}"; shift || true

case "$CMD" in
  all) make check ;;
  lint) make lint ;;
  typecheck) make typecheck ;;
  test)
    "${PY_RUN[@]}" pytest -q "$@"
    pnpm --filter @tt/web test; pnpm --filter @tt/desktop test ;;
  quick)
    "${PY_RUN[@]}" pytest -q -x apps/server/tests/unit packages/client_core/tests packages/schema/tests packages/filter_grammar/tests packages/cel_policies/tests "$@"
    pnpm --filter @tt/web test ;;
  e2e) make e2e ;;
  fmt) make fmt ;;
  -h|--help|help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown check '$CMD' (see scripts/check.sh --help)" ;;
esac
