#!/usr/bin/env bash
# Publish the internal planning material (plans, reviews, handover prompts) to a separate PRIVATE repository.
# The product repository keeps only the code and the how-to-use documentation.
#
#   scripts/repo/split-internal-docs.sh --repo OWNER/team_tracker-internal
#   options:
#     --from REF      commit to take the internal files from. Default: HEAD if they are still there, otherwise the
#                     last commit before they were removed (found automatically).
#     --history       keep the full git history of those paths (needs git-filter-repo: pip install git-filter-repo).
#                     Without it the private repo starts with one snapshot commit that names the source commit.
#     --no-protect    do not add branch protection to the new repo's main branch.
#     --dry-run       build the private repo locally (in .run/internal-repo) and stop before creating/pushing anything.
#
# Needs: git, and the GitHub CLI "gh" signed in with rights to create repositories (gh auth login).
# The list of internal paths is scripts/repo/internal-paths.txt.
#
# NOTE: removing files from the product repo does not remove them from its git history. If the product repo is or
# becomes public and the old plans must not be readable, rewrite its history too (docs/guide/07-repositories.md).
source "$(dirname "$0")/../lib/common.sh"
cd "$ROOT" || exit 1

REPO=""; FROM=""; HISTORY=0; PROTECT=1; DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift ;; --from) FROM="$2"; shift ;; --history) HISTORY=1 ;; --no-protect) PROTECT=0 ;;
    --dry-run) DRY=1 ;; -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; *) die "unknown option $1" ;;
  esac; shift
done
[[ -n "$REPO" || $DRY -eq 1 ]] || die "--repo OWNER/NAME is required (see --help)"
need git "Install git."

mapfile -t PATHS < <(grep -v -E '^\s*(#|$)' scripts/repo/internal-paths.txt)

# Which commit still has the internal files?
if [[ -z "$FROM" ]]; then
  if git cat-file -e "HEAD:${PATHS[0]}" 2>/dev/null; then FROM=HEAD
  else
    del="$(git log -1 --format=%H --diff-filter=D -- "${PATHS[0]}")"
    [[ -n "$del" ]] || die "cannot find a commit that contains ${PATHS[0]} - pass --from REF"
    FROM="$del^"
  fi
fi
SRC="$(git rev-parse "$FROM")"
info "taking ${PATHS[*]} from $(git log -1 --format='%h %s' "$SRC")"

WORK="$RUN_DIR/internal-repo"; rm -rf "$WORK"; mkdir -p "$RUN_DIR"

if [[ $HISTORY -eq 1 ]]; then
  have git-filter-repo || die "--history needs git-filter-repo (pip install git-filter-repo)"
  git clone --quiet --no-local "$ROOT" "$WORK"
  git -C "$WORK" checkout --quiet -B main "$SRC"
  fr_args=(); for p in "${PATHS[@]}"; do fr_args+=(--path "$p"); done
  git -C "$WORK" filter-repo --force "${fr_args[@]}" --refs main
else
  mkdir -p "$WORK"; git -C "$WORK" init --quiet -b main
  present=(); for p in "${PATHS[@]}"; do git cat-file -e "$SRC:$p" 2>/dev/null && present+=("$p"); done
  git archive "$SRC" "${present[@]}" | tar -x -C "$WORK"
fi

# A README for the private repo (only if it has none).
if [[ ! -f "$WORK/README.md" ]]; then
  cat > "$WORK/README.md" <<EOF
# Team Tracker - internal planning (private)

Implementation plans, review reports and handover prompts for the Team Tracker product.
The product code and its user documentation live in the product repository; this repository is the source the team
plans and implements from.

| Folder | What |
| --- | --- |
| \`plans/\` | phase plans and appendices - start at \`plans/README.md\` |
| \`docs/reviews/\` | implementation, technical and UX reviews with screenshots |
| \`docs/handover/\` | what is left after each implementation pass |
| \`tools/handover.py\` | fills the appendix-D handover prompts: \`python tools/handover.py kickoff 11\` |

Imported from the product repository at commit \`$SRC\`.
EOF
fi
(cd "$WORK" && git add -A && { git diff --cached --quiet || git -c user.name="${GIT_AUTHOR_NAME:-$(git -C "$ROOT" config user.name || echo tt)}" \
   -c user.email="${GIT_AUTHOR_EMAIL:-$(git -C "$ROOT" config user.email || echo tt@localhost)}" \
   commit --quiet -m "Import internal planning material from the product repository ($SRC)"; })
ok "private repo prepared in ${WORK#"$ROOT"/} ($(git -C "$WORK" rev-list --count HEAD) commit(s), $(find "$WORK" -type f -not -path '*/.git/*' | wc -l) files)"

if [[ $DRY -eq 1 ]]; then echo "dry run - nothing created or pushed"; exit 0; fi
need gh "Install the GitHub CLI (https://cli.github.com) and run: gh auth login"

if gh repo view "$REPO" >/dev/null 2>&1; then
  vis="$(gh repo view "$REPO" --json visibility -q .visibility)"
  [[ "$vis" == "PRIVATE" ]] || die "$REPO exists and is $vis - refusing to push internal plans to it"
  ok "$REPO exists and is private"
else
  gh repo create "$REPO" --private --description "Team Tracker - internal plans, reviews and handover (private)" >/dev/null
  ok "created private repository $REPO"
fi
git -C "$WORK" remote add origin "https://github.com/$REPO.git"
git -C "$WORK" push --quiet -u origin main
ok "pushed to https://github.com/$REPO"

if [[ $PROTECT -eq 1 ]]; then
  if gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null 2>&1 <<'JSON'
{"required_status_checks": null, "enforce_admins": false,
 "required_pull_request_reviews": {"required_approving_review_count": 1},
 "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
JSON
  then ok "main is protected (pull request + 1 approval, no force-push, no deletion)"
  else warn "could not protect main (private-repo branch protection needs GitHub Pro/Team). The repo is still private - only invited collaborators can see it."
  fi
fi
echo "Invite people:  gh api -X PUT repos/$REPO/collaborators/USERNAME -f permission=push"
