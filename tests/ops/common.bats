#!/usr/bin/env bats
# Unit tests of the script framework (scripts/lib/common.sh): flags, step runner, error format, exit codes, offline guard.
# Run: tests/ops/run.sh  (or: bats tests/ops/common.bats)

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  T="$BATS_TEST_TMPDIR"
  export NO_COLOR=1 TT_RUN_DIR="$T/run" TT_TOOLS_DIR="$T/tools" TT_LOG_STARTED= TT_LOG_FILE= TT_VERBOSE= TT_DRY_RUN= TT_YES= TT_QUIET= TT_OFFLINE=
  # a throw-away script built on the framework
  cat > "$ROOT/scripts/_bats_probe.sh" <<'SH'
#!/usr/bin/env bash
source "$(dirname "$0")/lib/common.sh"
usage() { echo "probe usage"; }
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
case "${1:-}" in
  args) echo "args=[$*] verbose=$TT_VERBOSE dry=$TT_DRY_RUN yes=$TT_YES quiet=$TT_QUIET" ;;
  step-ok) step "say hello" echo hello; ok "done" ;;
  step-fail) step "say hello" echo hello; step "break" bash -c 'for i in $(seq 1 60); do echo line$i; done; exit 7' ;;
  fail) fail "$2" "the what" "the why" "the fix" "the offline fix" ;;
  unexpected) false; echo never ;;
  run) run touch "$2"; echo "after" ;;
  offline) tt_need_network "downloading thing" thing --with-thing ;;
  offline-plain) tt_need_network "downloading thing" ;;
  confirm) if confirm "really?"; then echo yes; else echo no; fi ;;
  child) "$ROOT/scripts/_bats_probe.sh" fail TT-E201 ;;
  sub) x="$(resolve_components bogus)"; echo "x=$x" ;;
esac
SH
  chmod +x "$ROOT/scripts/_bats_probe.sh"
  P="$ROOT/scripts/_bats_probe.sh"
}
teardown() { rm -f "$ROOT/scripts/_bats_probe.sh"; }

@test "global flags are accepted anywhere and removed from the arguments" {
  run "$P" args -v one --dry-run two -y -q
  [ "$status" -eq 0 ]
  [[ "$output" == *"args=[args one two] verbose=1 dry=1 yes=1 quiet=1"* ]]
}
@test "-vv sets trace level 2; env equivalents work" {
  run env TT_VERBOSE=2 TT_DRY_RUN=1 "$P" args
  [[ "$output" == *"verbose=2 dry=1"* ]]
}
@test "--help prints the usage, the global options and the exit codes, exit 0" {
  run "$P" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"probe usage"* && "$output" == *"GLOBAL OPTIONS"* && "$output" == *"EXIT CODES"* ]]
}
@test "--log-file is honoured and the run log has full detail even when not verbose" {
  run "$P" step-ok --log-file "$T/my.log"
  [ "$status" -eq 0 ]
  grep -q "step 1: say hello" "$T/my.log"
  grep -q "^hello$" "$T/my.log"
}
@test "a run log is written by default in .run/logs/ttctl-<command>-<time>.log" {
  run "$P" step-ok
  ls "$TT_RUN_DIR"/logs/ttctl-_bats_probe-*.log
}
@test "step numbers steps and hides output unless -v" {
  run "$P" step-ok
  [[ "$output" == *"[1] say hello"* ]]
  [[ "$output" != *$'\nhello\n'* ]]
  run "$P" -v step-ok
  [[ "$output" == *"hello"* && "$output" == *"+ echo hello"* ]]
}
@test "a failing step prints the last 40 lines and a TT-E002 report with command, exit code and log" {
  run "$P" step-fail
  [ "$status" -eq 1 ]
  [[ "$output" == *"line60"* && "$output" == *"line21"* && "$output" != *"line20"$'\n'* ]]
  [[ "$output" == *"error TT-E002"* && "$output" == *"WHAT  step 2 \"break\" failed"* && "$output" == *"exited with code 7"* ]]
  [[ "$output" == *"LOG   "* ]]
}
@test "fail prints WHAT / WHY / FIX / DOCS and exits with the code's exit status" {
  run "$P" fail TT-E104;  [ "$status" -eq 3 ]
  [[ "$output" == *"error TT-E104  pnpm is missing"* && "$output" == *"WHAT  the what"* && "$output" == *"WHY   the why"* ]]
  [[ "$output" == *"FIX   the fix"* && "$output" == *"DOCS  docs/guide/11-troubleshooting.md#tt-e104"* ]]
  run "$P" fail TT-E010;  [ "$status" -eq 2 ]
  run "$P" fail TT-E201;  [ "$status" -eq 5 ]
  run "$P" fail TT-E400;  [ "$status" -eq 4 ]
  run "$P" fail TT-E200;  [ "$status" -eq 1 ]
}
@test "the offline variant of FIX is shown when TT_OFFLINE=1" {
  run env TT_OFFLINE=1 "$P" fail TT-E104
  [[ "$output" == *"FIX   the offline fix"* ]]
}
@test "an unexpected failure is reported by the ERR trap with command and script:line" {
  run "$P" unexpected
  [ "$status" -eq 1 ]
  [[ "$output" == *"error TT-E001"* && "$output" == *"WHAT  false"* && "$output" == *"_bats_probe.sh:"* ]]
  [[ "$output" != *"never"* ]]
}
@test "a failure inside a child script is reported once and keeps the child's exit code" {
  run "$P" child
  [ "$status" -eq 5 ]
  [ "$(grep -c 'error TT-E' <<<"$output")" -eq 1 ]
}
@test "a failure inside \$(...) is reported once" {
  run "$P" sub
  [ "$status" -eq 2 ]
  [ "$(grep -c 'error TT-E' <<<"$output")" -eq 1 ]
}
@test "--dry-run prints state-changing commands and runs nothing" {
  run "$P" run "$T/created" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run: touch"* ]]
  [ ! -e "$T/created" ]
}
@test "the offline guard refuses with exit 4 and names the bundle option" {
  run env TT_OFFLINE=1 "$P" offline
  [ "$status" -eq 4 ]
  [[ "$output" == *"error TT-E401"* && "$output" == *"--with-thing"* ]]
  run env TT_OFFLINE=1 "$P" offline-plain
  [ "$status" -eq 4 ]
  [[ "$output" == *"error TT-E400"* ]]
  run "$P" offline
  [ "$status" -eq 0 ]
}
@test "offline mode also sets the package managers' own offline variables" {
  run env TT_OFFLINE=1 bash -c "source '$ROOT/scripts/lib/common.sh'; echo \$UV_OFFLINE \$PIP_NO_INDEX \$npm_config_offline \$COREPACK_ENABLE_NETWORK \$PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"
  [[ "$output" == *"1 1 true 0 1"* ]]
}
@test "confirm: --yes answers yes; without a terminal it fails with TT-E013 (exit 2)" {
  run "$P" confirm --yes < /dev/null
  [[ "$output" == *"yes"* ]]
  run "$P" confirm < /dev/null
  [ "$status" -eq 2 ]
  [[ "$output" == *"error TT-E013"* ]]
}
@test "messages name ttctl, not an internal script" {
  run env TT_TTCTL=scripts/ttctl "$P" unexpected
  [[ "$output" == *"FIX   re-run with -v to see every command: scripts/ttctl"* ]]
}
@test "secrets are masked in the effective configuration shown by -v" {
  run env TT_SESSION_SECRET=abc123 TT_DATABASE_URL=postgresql+psycopg://u:pw@h/db "$P" -v args
  [[ "$output" == *"TT_SESSION_SECRET=***"* && "$output" == *"postgresql+psycopg://u:***@h/db"* ]]
  [[ "$output" != *"abc123"* && "$output" != *":pw@"* ]]
}
