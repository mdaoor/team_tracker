"""Rewrite references from product documentation to files that moved to the private planning repository.

    python scripts/repo/rewrite_internal_refs.py [--check] FILE...

* a markdown link to a moved file keeps its text and loses the link
* an ADR path (docs/decisions/ADR-0046-....md) becomes "ADR-0046"
* any other moved path becomes "<name> (internal planning repository)"
--check only reports (exit 1 when something would change); tests/ops uses it so new references cannot creep in.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
paths = [
    line.strip() for line in open(os.path.join(ROOT, "scripts/repo/internal-paths.txt"))
    if line.strip() and not line.startswith("#")
]
alt = "|".join(re.escape(p) for p in sorted(paths, key=len, reverse=True))
# optional ../ prefixes and an optional leading "docs/" already contained in the path list
PATH = rf"(?:\.\./)*(?:{alt})(?:/[A-Za-z0-9_.\-/]*)?(?:#[A-Za-z0-9_\-.]*)?"


def label(p: str) -> str:
    p = p.split("#")[0]
    m = re.search(r"(ADR-\d{4})", p)
    if m:
        return m.group(1)
    name = os.path.basename(p.rstrip("/")) or p
    return f"{name} (internal planning repository)"


def rewrite(text: str, rel_dir: str) -> str:
    def resolve(target: str) -> str:
        # make relative links comparable with the repo-relative list
        norm = os.path.normpath(os.path.join(rel_dir, target)) if target.startswith("..") or not target.startswith(("docs/", "plans", "tools/", "apps/")) else target
        return norm
    def link(m: re.Match) -> str:
        txt, target = m.group(1), m.group(2)
        t = resolve(target.split("#")[0])
        if re.fullmatch(PATH, t) or re.fullmatch(PATH, target):
            return txt if txt.strip("`") != target else label(target)
        return m.group(0)
    text = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", link, text)
    text = re.sub(rf"`({PATH})`", lambda m: f"`{label(m.group(1))}`" if "ADR-" in m.group(1) else label(m.group(1)), text)
    # bare paths only when they contain a "/" (so the English word "plans" is left alone)
    text = re.sub(rf"(?<![\w/`])(?={PATH})(?=[^\s]*/){PATH}", lambda m: label(m.group(0)), text)
    return text


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    files = [a for a in args if a != "--check"]
    changed = []
    for f in files:
        src = open(f, encoding="utf-8").read()
        out = rewrite(src, os.path.relpath(os.path.dirname(os.path.abspath(f)), ROOT))
        if out != src:
            changed.append(f)
            if not check:
                open(f, "w", encoding="utf-8").write(out)
    for f in changed:
        print(("would rewrite " if check else "rewrote ") + f)
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
