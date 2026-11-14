#!/usr/bin/env bash
# Scan a git repository (working tree AND full history) for committed secrets before it is published or split.
source "$(dirname "$0")/../lib/common.sh"
usage() {
  cat <<EOF
ttctl repo secrets-scan - look for committed secrets in a repository's files and history

SYNOPSIS
  $TT_TTCTL repo secrets-scan [REPO_DIR] [--report FILE]

HOW
  gitleaks when installed (gitleaks detect --log-opts=--all); otherwise detect-secrets (from PyPI, via uvx) over the
  working tree and over every patch in the history (git log -p --all). Findings print with file, line/commit and type;
  the exit code is 1 when anything was found. Known test fixtures (dev passwords "tt", dummy tokens in tests) are
  reported too: review them, they are not ignored silently.

EXAMPLES
  $TT_TTCTL repo secrets-scan
  $TT_TTCTL repo secrets-scan .run/internal-repo --report .run/secrets-internal.json
  $TT_TTCTL -v repo secrets-scan /path/to/another/checkout

SEE ALSO
  $GUIDE/14-repositories.md#secrets-scan
EOF
}
tt_init "$@"; set -- "${TT_ARGS[@]+"${TT_ARGS[@]}"}"
REPO="$ROOT"; REPORT=""
while [[ $# -gt 0 ]]; do case "$1" in --report) REPORT="${2:?}"; shift ;; -*) usage_fail "unknown option '$1'" ;; *) REPO="$(cd "$1" && pwd)" ;; esac; shift; done
[[ -d "$REPO/.git" ]] || usage_fail "$REPO is not a git repository"
[[ -n "$REPORT" ]] || REPORT="$RUN_DIR/secrets-$(basename "$REPO")-$(date +%Y%m%d-%H%M%S).json"
run mkdir -p "$(dirname "$REPORT")"
if have gitleaks; then
  info "gitleaks over files and the full history of $REPO"
  if gitleaks detect --source "$REPO" --log-opts=--all --report-format json --report-path "$REPORT" --redact --no-banner; then ok "no secrets found (gitleaks)"; exit 0; fi
  warn "gitleaks found secrets - report: $REPORT"; exit 1
fi
tt_need_network "detect-secrets (from PyPI)" "" ""
info "detect-secrets over the working tree and every commit of $REPO (gitleaks not installed)"
tmp="$(mktemp -d)"
git -C "$REPO" log -p --all --no-color --format='commit %H' > "$tmp/history.patch"
( cd "$REPO" && uvx -q --from detect-secrets detect-secrets scan --all-files --exclude-files '(\.venv|node_modules|\.tools|dist|\.run)/' . ) > "$tmp/tree.json"
( cd "$tmp" && uvx -q --from detect-secrets detect-secrets scan history.patch ) > "$tmp/hist.json"
rc=0
"$(tt_python)" - "$tmp/tree.json" "$tmp/hist.json" "$tmp/history.patch" "$REPORT" <<'PY' || rc=$?
import json, sys
tree, hist, patch, out = sys.argv[1:]
res = []
for f, items in json.load(open(tree))["results"].items():
    for i in items: res.append({"where": "tree", "file": f, "line": i["line_number"], "type": i["type"]})
lines = open(patch, encoding="utf-8", errors="replace").read().splitlines()
for items in json.load(open(hist))["results"].values():
    for i in items:
        n = i["line_number"] - 1; commit = file = "?"
        for j in range(n, -1, -1):
            if file == "?" and lines[j].startswith("+++ b/"): file = lines[j][6:]
            if lines[j].startswith("commit "): commit = lines[j][7:15]; break
        res.append({"where": "history", "commit": commit, "file": file, "type": i["type"]})
json.dump(res, open(out, "w"), indent=1)
seen = set()
for r in res:
    k = (r["where"], r.get("commit"), r["file"], r["type"])
    if k in seen: continue
    seen.add(k); print(f'  {r["where"]:<8} {r.get("commit", ""):<9} {r["file"]}  [{r["type"]}]')
print(f"{len(res)} finding(s) in {len(seen)} place(s)")
sys.exit(1 if res else 0)
PY
rm -rf "$tmp"
if [[ $rc -eq 0 ]]; then ok "no secrets found (detect-secrets)"; else warn "review the findings above - full report: ${REPORT#"$ROOT"/}"; fi
exit $rc
