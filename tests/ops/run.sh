#!/usr/bin/env bash
# Ops tests: shellcheck on every script, bats unit tests of the framework, pytest checks of help, docs and error codes.
#   tests/ops/run.sh            (also: scripts/ttctl check ops)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
mapfile -t SH < <(git ls-files 'scripts/*.sh' 'scripts/**/*.sh' 'ci/ops/*.sh' 'tests/ops/*.sh' scripts/ttctl deploy/backup/*.sh)
echo "== shellcheck (${#SH[@]} scripts)"
if command -v shellcheck >/dev/null; then shellcheck -x -S warning "${SH[@]}"; else uvx -q --from shellcheck-py shellcheck -x -S warning "${SH[@]}"; fi
echo "== bats"
if command -v bats >/dev/null; then bats tests/ops; else npx --yes bats@1.11.0 tests/ops; fi
echo "== pytest tests/ops"
uv run --no-sync python -m pytest -q tests/ops
