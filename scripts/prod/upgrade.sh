#!/usr/bin/env bash
# Upgrade and roll back a production install.
source "$(dirname "$0")/../lib/common.sh"
usage() {
  cat <<EOF
ttctl prod upgrade / rollback / releases - upgrade with a backup first, roll back to the previous release

SYNOPSIS
  sudo $TT_TTCTL prod upgrade [GIT_REF] [--no-backup]          online: git checkout, deps, build, migrate, restart
  sudo $TT_TTCTL prod upgrade --from-bundle FILE [--no-backup] offline: new release dir from the bundle, migrate, switch
  sudo $TT_TTCTL prod rollback                                 back to the previous release (or the previous commit)
  $TT_TTCTL prod releases                                      list installed releases (bundle installs)

HOW
  1 backup the database (/var/backups/tt)   2 install the new code next to the running one
  3 migrate (migrations are additive: the old code still runs on the new schema)
  4 switch (bundle: the PREFIX/current symlink; git: the checkout) and restart   5 health check
  If the health check fails, the previous release is switched back and restarted automatically (exit 1, TT-E301).

EXAMPLES
  sudo $TT_TTCTL prod upgrade v1.1.0
  sudo /opt/team-tracker/current/scripts/ttctl prod upgrade --from-bundle /media/usb/tt-bundle-1.1.0-rhel8-x86_64.tar.zst
  sudo $TT_TTCTL prod rollback
  $TT_TTCTL prod releases

SEE ALSO
  $GUIDE/07-upgrade-and-rollback.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
VERB="${1:-upgrade}"; case "$VERB" in upgrade|rollback|releases) shift ;; *) VERB=upgrade ;; esac
BACKUP=1; REF=""; BUNDLE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-backup) BACKUP=0 ;; --from-bundle) BUNDLE="${2:-}"; shift ;;
    -*) usage_fail "unknown option '$1' for prod $VERB" ;; *) REF="$1" ;;
  esac; shift
done
ENVFILE=/etc/tt/tt.env
# A bundle install lives in PREFIX/releases/VERSION with PREFIX/current -> it.
PREFIX=""; [[ "$ROOT" == */releases/* ]] && PREFIX="${ROOT%/releases/*}"

need_root() { [[ "$(id -u)" -eq 0 ]] || dry_run || fail TT-E020 "prod $VERB changes the installed release" "you are $(id -un), not root" "sudo $TT_TTCTL prod $VERB"; }
load_prod_env() { [[ -f "$ENVFILE" ]] || fail TT-E500 "$ENVFILE is missing" "this host has no production install" "sudo $TT_TTCTL prod install --mode proxy"; set -a; # shellcheck disable=SC1090
  source "$ENVFILE"; set +a; }
healthy() { local _; for _ in $(seq 1 60); do "$ROOT/scripts/prod/service.sh" health >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
backup() {
  [[ $BACKUP -eq 1 ]] || { warn "no backup (--no-backup)"; return; }
  step "back up the database" env BACKUP_DIR=/var/backups/tt "$ROOT/deploy/backup/backup.sh"
}

case "$VERB" in
  releases)
    [[ -n "$PREFIX" ]] || { say "not a bundle install (code in $ROOT, commit $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?'))"; exit 0; }
    cur="$(readlink -f "$PREFIX/current" 2>/dev/null || true)"; prev="$(readlink -f "$PREFIX/previous" 2>/dev/null || true)"
    for r in "$PREFIX"/releases/*/; do r="${r%/}"; m=""; [[ "$r" == "$cur" ]] && m="  <- current"; [[ "$r" == "$prev" ]] && m="  <- previous"; say "$(basename "$r")$m"; done ;;

  rollback)
    need_root
    if [[ -n "$PREFIX" ]]; then
      [[ -L "$PREFIX/previous" ]] || fail TT-E012 "there is no previous release to roll back to" "$PREFIX/previous does not exist" "$TT_TTCTL prod releases   (install an older bundle with prod upgrade --from-bundle)"
      cur="$(readlink -f "$PREFIX/current")"; prev="$(readlink -f "$PREFIX/previous")"
      run ln -sfn "$prev" "$PREFIX/current"; run ln -sfn "$cur" "$PREFIX/previous"
      run "$ROOT/scripts/prod/service.sh" restart
      dry_run || healthy || fail TT-E301 "the rolled-back release $(basename "$prev") is not healthy" "no answer on /readyz for 60 s" "$TT_TTCTL prod logs tt-server"
      ok "rolled back to $(basename "$prev") (previous is now $(basename "$cur"))"
    else
      prevc="$(cat "$RUN_DIR/previous-commit" 2>/dev/null || true)"
      [[ -n "$prevc" ]] || fail TT-E012 "no previous commit recorded" "$RUN_DIR/previous-commit is missing (no upgrade ran here)" "sudo $TT_TTCTL prod upgrade <commit>"
      exec "$0" upgrade "$prevc" --no-backup
    fi ;;

  upgrade)
    need_root; load_prod_env
    if [[ -n "$BUNDLE" ]]; then
      [[ -n "$PREFIX" ]] || fail TT-E500 "--from-bundle needs a bundle install (PREFIX/releases/VERSION)" "this code runs from $ROOT" \
        "install the first release from a bundle: ./install.sh --mode prod"
      [[ -f "$BUNDLE" ]] || usage_fail "no such bundle file: $BUNDLE"
      title "Upgrade from $(basename "$BUNDLE")"
      old="$(readlink -f "$PREFIX/current")"
      newrel="$("$ROOT/scripts/bundle.sh" install "$BUNDLE" --prefix "$PREFIX" --mode prod --upgrade | tail -1)"
      dry_run && exit 0
      [[ -d "$newrel/.venv" ]] || fail TT-E404 "installing the new release failed" "$newrel has no .venv (output above)" "$TT_TTCTL bundle verify $BUNDLE"
      [[ "$newrel" == "$old" ]] && fail TT-E012 "release $(basename "$newrel") is already the current one" "the bundle has the same version" "$TT_TTCTL prod releases"
      backup
      step "migrate with $(basename "$newrel")" runuser -u tt --preserve-environment -- "$newrel/.venv/bin/tt-admin" migrate
      run ln -sfn "$newrel" "$PREFIX/current"; run ln -sfn "$old" "$PREFIX/previous"
      "$newrel/scripts/prod/service.sh" restart
      if healthy; then ok "upgraded $(basename "$old") -> $(basename "$newrel")"; say "Roll back with: sudo $TT_TTCTL prod rollback"
      else
        warn "new release not healthy - switching back to $(basename "$old")"
        ln -sfn "$old" "$PREFIX/current"; ln -sfn "$newrel" "$PREFIX/previous"
        "$old/scripts/prod/service.sh" restart || true
        fail TT-E301 "release $(basename "$newrel") did not become healthy; $(basename "$old") is running again" "no answer on /readyz for 60 s after the switch" \
          "read the logs ($TT_TTCTL prod logs tt-server), fix, then retry the upgrade"
      fi
    else
      tt_need_network "git upgrade" "" ""
      have git || fail TT-E100 "git upgrade needs git" "git is not on PATH" "$(sudo_prefix)dnf -y install git"
      cd "$ROOT" || exit 1
      before="$(git rev-parse HEAD)"
      backup
      step "fetch" git fetch --tags origin
      if [[ -n "$REF" ]]; then step "checkout $REF" git checkout -q "$REF"; else step "pull" git pull --ff-only -q; fi
      dry_run || echo "$before" > "$RUN_DIR/previous-commit"
      step "Python dependencies" uv sync --all-packages --no-dev --frozen
      step "pnpm install" pnpm install --frozen-lockfile
      step "build the deck viewer" pnpm --filter @tt/presentation build
      step "build the web console" pnpm --filter @tt/web build
      run chmod -R a+rX "$ROOT"
      step "migrate" runuser -u tt --preserve-environment -- "$ROOT/.venv/bin/tt-admin" migrate
      run "$ROOT/scripts/prod/service.sh" restart
      dry_run && exit 0
      if healthy; then ok "upgraded ${before:0:8} -> $(git rev-parse --short HEAD)"; say "Roll back with: sudo $TT_TTCTL prod rollback"
      else
        warn "not healthy after the upgrade - going back to ${before:0:8}"
        git checkout -q "$before"; uv sync --all-packages --no-dev --frozen -q; pnpm --filter @tt/web build >/dev/null 2>&1 || true
        "$ROOT/scripts/prod/service.sh" restart || true
        fail TT-E301 "the upgrade did not become healthy; ${before:0:8} is running again" "no answer on /readyz for 60 s" "$TT_TTCTL prod logs tt-server"
      fi
    fi ;;
esac
