#!/usr/bin/env bash
# Regression test for the reported failure: RHEL 8 host with Python 3.13, uv, Node 22, curl 7.61, psql 10.23, NO pnpm,
# no terminal. "ttctl setup --with-db" must: offer the install (name the command), name "scripts/ttctl setup" (not an
# internal script), explain that psql 10 is fine for --with-db, exit 3; with -v it must show what was probed.
#   ci/ops/repro-rhel8.sh
source "$(dirname "$0")/lib.sh"
ci_prepare_ca "$CI_ROOT/ci/ops/images"
mapfile -t BF < <(ci_build_flags)
docker build -q "${BF[@]}" -t tt-repro-rhel8 -f "$CI_ROOT/ci/ops/images/rhel8-repro.Dockerfile" "$CI_ROOT/ci/ops/images" >/dev/null
SRC="$(mktemp -d)"; ci_snapshot "$SRC/src"; trap 'rm -rf "$SRC"' EXIT
fail=0
expect() { if grep -qE -- "$2" <<<"$OUT"; then echo "  ok   $1"; else echo "  FAIL $1 (pattern: $2)"; fail=1; fi; }
reject() { if grep -qE -- "$2" <<<"$OUT"; then echo "  FAIL $1 (found: $2)"; fail=1; else echo "  ok   $1"; fi; }

echo "== scripts/ttctl setup --with-db  (no terminal, no --yes)"
OUT="$(docker run --rm -v "$SRC/src:/src" tt-repro-rhel8 bash -c 'NO_COLOR=1 scripts/ttctl setup --with-db < /dev/null; echo "exit=$?"' 2>&1)"
echo "$OUT" | sed 's/^/   | /'
expect "exits with 3 (prerequisite missing)" '^exit=3$'
expect "offers the install with a runnable command" 'FIX +scripts/ttctl setup --install-missing --with-db'
expect "names the single-tool install too" 'scripts/ttctl prereqs install pnpm'
expect "names scripts/ttctl, never an internal script" 'scripts/ttctl'
reject "does not tell the user to run setup.sh" 'scripts/setup\.sh'
expect "explains the psql 10 finding for --with-db" "psql 10\.23 found\. That is fine for setup and --with-db"
expect "uses the WHAT/WHY/FIX/DOCS format" 'DOCS +docs/guide/11-troubleshooting\.md#tt-e100'
expect "points at the run log" 'LOG +\.run/logs/ttctl-setup-'
reject "no offline-incompatible hint (corepack network download)" 'corepack prepare'

echo "== scripts/ttctl -v setup --with-db  (shows what was probed)"
OUT="$(docker run --rm -v "$SRC/src:/src" tt-repro-rhel8 bash -c 'NO_COLOR=1 scripts/ttctl -v setup --with-db < /dev/null; echo "exit=$?"' 2>&1)"
expect "shows the psql probe with its raw output" 'probe: psql --version -> rc=0 \| psql \(PostgreSQL\) 10\.23'
expect "shows the node probe with its path" 'tool node: status=ok version=22\.11\.0 path=/usr/local/bin/node'
expect "shows the effective configuration with secrets masked" 'TT_DATABASE_URL=postgresql\+psycopg://tt:\*\*\*@'
expect "still exits with 3" '^exit=3$'

echo "== offline: TT_OFFLINE=1 must not suggest a download"
OUT="$(docker run --rm -v "$SRC/src:/src" -e TT_OFFLINE=1 tt-repro-rhel8 bash -c 'NO_COLOR=1 scripts/ttctl setup --with-db < /dev/null; echo "exit=$?"' 2>&1)"
expect "exits with 4 (blocked because offline)" '^exit=4$'
expect "fix points at the bundle" 'prereqs install pnpm --from-bundle DIR'
[[ $fail -eq 0 ]] && echo "REGRESSION TEST PASSED" || { echo "REGRESSION TEST FAILED"; exit 1; }
