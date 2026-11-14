#!/usr/bin/env bash
# Publish the internal planning material to a separate PRIVATE repository.
source "$(dirname "$0")/../lib/common.sh"
usage() {
  cat <<EOF
ttctl repo split - publish plans, reviews, decisions and design material to a private, protected repository

SYNOPSIS
  $TT_TTCTL repo split --repo OWNER/NAME [--history] [--from REF] [--no-protect] [--no-scan] [--dry-run]

WHAT
  The paths in scripts/repo/internal-paths.txt are copied - each from the last commit that still had it - into a new
  repository, together with an overlay (README explaining the layout, CODEOWNERS, the handover tool adapted to read
  the product checkout from TT_PRODUCT_DIR). Then a secrets scan runs over the result. Only then the repository is
  created PRIVATE (an existing one must already be private), main is pushed and protected: pull request with one
  approval, no force-push, no deletion.

OPTIONS
  --repo OWNER/NAME  target (e.g. mdaoor/team_tracker-internal)
  --history          keep the full git history of those paths (needs git-filter-repo); default: one snapshot commit
  --from REF         take every path from this commit instead
  --no-protect       skip branch protection         --no-scan    skip the secrets scan (not recommended)
  --dry-run          build it locally in .run/internal-repo and stop: nothing is created or pushed
  Needs: git; for publishing, the GitHub CLI signed in with rights to create repositories (gh auth login).
  Removing files from the product repository does not remove them from its history: see
  $TT_TTCTL help repo purge-history and $GUIDE/14-repositories.md#history.

EXAMPLES
  $TT_TTCTL repo split --repo mdaoor/team_tracker-internal --history --dry-run
  $TT_TTCTL repo split --repo mdaoor/team_tracker-internal --history
  $TT_TTCTL repo split --repo mdaoor/team_tracker-internal --from 31222e5

SEE ALSO
  $GUIDE/14-repositories.md
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
# --dry-run here = build and verify locally (its only write is .run/internal-repo), publish nothing
SPLIT_DRY="$TT_DRY_RUN"; TT_DRY_RUN=0
REPO=""; FROM=""; HISTORY=0; PROTECT=1; SCAN=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift ;; --from) FROM="${2:-}"; shift ;; --history) HISTORY=1 ;; --no-protect) PROTECT=0 ;; --no-scan) SCAN=0 ;;
    *) usage_fail "unknown option '$1' for repo split" ;;
  esac; shift
done
[[ -n "$REPO" || "$SPLIT_DRY" == 1 ]] || usage_fail "--repo OWNER/NAME is required" "or use --dry-run to only build it locally"
have git || fail TT-E100 "repo split needs git" "git is not on PATH" "$(sudo_prefix)dnf -y install git"
mapfile -t PATHS < <(grep -v -E '^\s*(#|$)' scripts/repo/internal-paths.txt)

# The commit that last had PATH: HEAD when it is still there, else the parent of the commit that deleted it.
source_of() {
  local p="$1" del
  [[ -n "$FROM" ]] && { git rev-parse "$FROM"; return; }
  if git cat-file -e "HEAD:$p" 2>/dev/null; then git rev-parse HEAD; return; fi
  del="$(git log -1 --format=%H --diff-filter=D -- "$p")"
  [[ -n "$del" ]] && git rev-parse "$del^" || echo ""
}

WORK="$RUN_DIR/internal-repo"
run rm -rf "$WORK"
if [[ $HISTORY -eq 1 ]]; then
  have git-filter-repo || fail TT-E100 "--history needs git-filter-repo" "git-filter-repo is not installed" \
    "pip install --user git-filter-repo   (or: uv tool install git-filter-repo), or leave out --history for a snapshot"
  # every path's history up to its last commit: filter a clone of the newest commit that has any of them (all branches merged in)
  tip="$(for p in "${PATHS[@]}"; do source_of "$p"; done | grep . | xargs -n1 git log -1 --format='%ct %H' | sort -n | tail -1 | cut -d' ' -f2)"
  step "clone the product repository" git clone --quiet --no-local "$ROOT" "$WORK"
  step "branch at $(git rev-parse --short "$tip")" git -C "$WORK" checkout --quiet -B main "$tip"
  # only main goes into the private repository: drop every other branch, remote-tracking ref and tag first
  step "drop all other refs" bash -c "git -C '$WORK' for-each-ref --format='%(refname)' | grep -v '^refs/heads/main\$' | xargs -r -n1 git -C '$WORK' update-ref -d"
  fr=(); for p in "${PATHS[@]}"; do fr+=(--path "$p"); done
  step "keep only the internal paths and their history" git -C "$WORK" filter-repo --force "${fr[@]}"
  run git -C "$WORK" remote remove origin 2>/dev/null || true
  # paths deleted before $tip are not in its tree: restore their last content on top
  for p in "${PATHS[@]}"; do
    src="$(source_of "$p")"; [[ -n "$src" ]] || continue
    if ! git -C "$WORK" cat-file -e "HEAD:$p" 2>/dev/null; then
      step "restore $p from $(git rev-parse --short "$src")" bash -c "git -C '$ROOT' archive '$src' '$p' | tar -xf - -C '$WORK'"
    fi
  done
else
  run mkdir -p "$WORK"; run git -C "$WORK" init --quiet -b main
  for p in "${PATHS[@]}"; do
    src="$(source_of "$p")"; [[ -n "$src" ]] || { warn "$p: not found in any commit - skipped"; continue; }
    step "$p from $(git rev-parse --short "$src")" bash -c "git archive '$src' '$p' | tar -xf - -C '$WORK'"
  done
fi
step "overlay: README, CODEOWNERS, .gitignore" cp -a scripts/repo/internal-overlay/README.md scripts/repo/internal-overlay/.github scripts/repo/internal-overlay/.gitignore "$WORK/"
step "handover tool reads the product from TT_PRODUCT_DIR" "$(tt_python)" scripts/repo/internal-overlay/patch_handover.py "$WORK"
srcs="$(for p in "${PATHS[@]}"; do printf '%s@%s ' "$p" "$(git rev-parse --short "$(source_of "$p")" 2>/dev/null)"; done)"
step "commit" bash -c "cd '$WORK' && git add -A && git -c user.name='${GIT_AUTHOR_NAME:-$(git -C "$ROOT" config user.name || echo ttctl)}' \
  -c user.email='${GIT_AUTHOR_EMAIL:-$(git -C "$ROOT" config user.email || echo ttctl@localhost)}' commit --quiet -m 'Import internal planning material from mdaoor/team_tracker

Sources: $srcs'"
ok "private repository prepared in ${WORK#"$ROOT"/}: $(git -C "$WORK" rev-list --count HEAD) commit(s), $(git -C "$WORK" ls-files | wc -l) files"

if [[ $SCAN -eq 1 ]]; then
  "$ROOT/scripts/repo/secrets-scan.sh" "$WORK" || [[ "$SPLIT_DRY" == 1 ]] || fail TT-E600 "the secrets scan found something in the private repository content" \
    "publishing stops until the findings above are reviewed" "review the report; if they are test fixtures, re-run with --no-scan; otherwise remove them first"
fi
if [[ "$SPLIT_DRY" == 1 || -z "$REPO" ]]; then
  say ""; say "Dry run: nothing created or pushed. The owner publishes it with:"
  say "  gh auth login"
  say "  $TT_TTCTL repo split --repo ${REPO:-mdaoor/team_tracker-internal}$( [[ $HISTORY -eq 1 ]] && echo ' --history')"
  exit 0
fi

have gh || fail TT-E100 "publishing needs the GitHub CLI" "gh is not on PATH" "install it (https://cli.github.com), run gh auth login, then run this again"
if gh repo view "$REPO" >/dev/null 2>&1; then
  vis="$(gh repo view "$REPO" --json visibility -q .visibility)"
  [[ "$vis" == "PRIVATE" ]] || fail TT-E600 "$REPO exists and is $vis" "internal plans must never be pushed to a non-private repository" "make it private in its settings, or choose another --repo"
  ok "$REPO exists and is private"
else
  run gh repo create "$REPO" --private --description "Team Tracker - internal plans, reviews, decisions (private)" \
    || fail TT-E600 "could not create $REPO" "gh refused (no rights to create repositories for this owner?)" \
       "ask the owner to run: $TT_TTCTL repo split --repo $REPO$( [[ $HISTORY -eq 1 ]] && echo ' --history')"
  ok "created private repository $REPO"
fi
run git -C "$WORK" remote add origin "https://github.com/$REPO.git"
run git -C "$WORK" push --quiet -u origin main
ok "pushed to https://github.com/$REPO"
if [[ $PROTECT -eq 1 ]]; then
  if gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null 2>&1 <<'JSON'
{"required_status_checks": null, "enforce_admins": true,
 "required_pull_request_reviews": {"required_approving_review_count": 1, "require_code_owner_reviews": true},
 "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
JSON
  then ok "main is protected (pull request + 1 code-owner approval, no force-push, no deletion)"
  else warn "could not protect main (branch protection on private repositories needs GitHub Pro/Team); the repository is private"; fi
fi
say "Invite people: gh api -X PUT repos/$REPO/collaborators/USERNAME -f permission=push"
