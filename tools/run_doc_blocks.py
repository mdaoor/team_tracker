"""Extract (and run) the command blocks of the guide that are marked runnable, so CI follows the guide word for word.

A fenced block whose info string is "bash runnable" runs on a connected host (ci/ops/online.sh); "bash runnable-offline"
runs on the air-gapped host from the unpacked bundle's directory (ci/ops/airgap.sh). Blocks run in page order, one shell,
`set -euo pipefail`, so a `cd` in one block holds for the next.

    python tools/run_doc_blocks.py --tag runnable docs/guide/01-requirements.md docs/guide/02-install-online.md   # print
    python tools/run_doc_blocks.py --tag runnable --list docs/guide/*.md                                        # list
    python tools/run_doc_blocks.py --tag runnable --run docs/guide/02-install-online.md                         # run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FENCE = re.compile(r"^```bash (runnable(?:-offline)?)\s*\n(.*?)^```", re.M | re.S)


def blocks(path: Path, tag: str) -> list[str]:
    return [m.group(2).rstrip() for m in FENCE.finditer(path.read_text()) if m.group(1) == tag]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True, choices=["runnable", "runnable-offline"])
    p.add_argument("--run", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("pages", nargs="+", type=Path)
    a = p.parse_args()
    script = ["set -euo pipefail"]
    for page in a.pages:
        for i, b in enumerate(blocks(page, a.tag), 1):
            if a.list:
                print(f"{page}:{i}: {b.splitlines()[0]}")
            script += [f"echo; echo '### {page.name} block {i}: {b.splitlines()[0][:90]}'", b]
    if a.list:
        return 0
    text = "\n".join(script) + "\n"
    if not a.run:
        sys.stdout.write(text)
        return 0
    return subprocess.run(["bash", "-c", text], check=False).returncode  # noqa: S603,S607


if __name__ == "__main__":
    sys.exit(main())
