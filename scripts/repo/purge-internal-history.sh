#!/usr/bin/env bash
# Prepare (and only on explicit request run) a rewrite of this repository's history without the internal paths.
source "$(dirname "$0")/../lib/common.sh"
CONFIRM_FLAG="--i-understand-this-rewrites-public-history"
usage() {
  cat <<EOF
ttctl repo purge-history - report, and only on explicit request perform, removing the internal paths from ALL history

SYNOPSIS
  $TT_TTCTL repo purge-history                                  report only (default): what a rewrite would change
  $TT_TTCTL repo purge-history $CONFIRM_FLAG
                                                                rewrite a MIRROR clone in .run/purge-mirror; push nothing
  (pushing is a separate, manual step printed at the end - this script never force-pushes)

WHY THIS IS NOT AUTOMATIC
  Removing files from the tip leaves them in every older commit, every branch and every merged pull request. A rewrite
  changes every commit id: every clone, fork and open pull request breaks, and GitHub keeps the old pull request refs
  (refs/pull/*) and cached views until GitHub Support purges them. Choose between the three options in
  $GUIDE/14-repositories.md#history before running anything; the recommended one does not need this script.

REPORT
  the internal paths (scripts/repo/internal-paths.txt), how many commits and which branches and pull-request refs
  contain them, and the exact commands of the rewrite.

EXAMPLES
  $TT_TTCTL repo purge-history
  $TT_TTCTL -v repo purge-history
  $TT_TTCTL repo purge-history $CONFIRM_FLAG

SEE ALSO
  $GUIDE/14-repositories.md#history
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
cd "$ROOT" || exit 1
DO=0
for a in "$@"; do case "$a" in "$CONFIRM_FLAG") DO=1 ;; *) usage_fail "unknown option '$a'" "the only option is $CONFIRM_FLAG" ;; esac; done
mapfile -t PATHS < <(grep -v -E '^\s*(#|$)' scripts/repo/internal-paths.txt)
URL="$(git remote get-url origin 2>/dev/null || echo '<origin>')"

title "What a history rewrite would remove"
say "paths: ${PATHS[*]}"
n="$(git rev-list --all --count -- "${PATHS[@]}" 2>/dev/null || echo '?')"
say "commits touching them (all local refs): $n"
say "branches whose tip still contains any of them:"
while IFS= read -r b; do
  for p in "${PATHS[@]}"; do if git cat-file -e "$b:$p" 2>/dev/null; then say "  $b"; break; fi; done
done < <(git for-each-ref --format='%(refname:short)' refs/heads refs/remotes | grep -v '/HEAD$')
pr="$(git ls-remote origin 'refs/pull/*/head' 2>/dev/null | wc -l || echo 0)"
say "pull-request refs on the remote: $pr (read-only for you; only GitHub Support can purge them)"

if [[ $DO -eq 0 ]]; then
  title "Nothing was changed"
  say "To rewrite (after choosing option (c) in $GUIDE/14-repositories.md#history):"
  say "  $TT_TTCTL repo purge-history $CONFIRM_FLAG"
  exit 0
fi

have git-filter-repo || fail TT-E100 "the rewrite needs git-filter-repo" "it is not installed" "pip install --user git-filter-repo"
MIRROR="$RUN_DIR/purge-mirror"
warn "rewriting a MIRROR clone in ${MIRROR#"$ROOT"/} - your checkout and the remote stay untouched"
run rm -rf "$MIRROR"
step "mirror clone of $URL" git clone --quiet --mirror "$URL" "$MIRROR"
inv=(); for p in "${PATHS[@]}"; do inv+=(--path "$p"); done
step "remove the internal paths from every commit" git -C "$MIRROR" filter-repo --force --invert-paths "${inv[@]}"
left="$(git -C "$MIRROR" rev-list --all --count -- "${PATHS[@]}")"
[[ "$left" == 0 ]] || fail TT-E600 "$left commit(s) still contain internal paths after the rewrite" "filter-repo did not remove everything" "inspect $MIRROR with: git -C $MIRROR log --all -- ${PATHS[0]}"
ok "rewritten mirror has no internal paths in any commit"
title "Not pushed. To publish the rewrite (breaks every clone, fork and open PR):"
say "  1. announce a freeze; merge or close open pull requests"
say "  2. git -C $MIRROR push --force --mirror $URL"
say "  3. ask GitHub Support to purge cached views and refs/pull/* of the old commits"
say "  4. everyone re-clones; delete old forks"
