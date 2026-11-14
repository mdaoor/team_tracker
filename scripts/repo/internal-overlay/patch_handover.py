"""Adapt tools/handover.py and its test to the private planning repository (run by split-internal-docs.sh).

* ROOT stays the planning repository (plans/ live there)
* TT_PRODUCT_DIR (default ../team_tracker next to it) is the product checkout the prompts point the implementer at:
  its git remote is the repository URL in the prompt, and its HEAD commit is stamped as the commit the plan targets
* the test moves to tests/test_handover.py
"""

import re
import sys
from pathlib import Path

repo = Path(sys.argv[1])
h = repo / "tools" / "handover.py"
s = h.read_text()
s = s.replace(
    "ROOT = Path(__file__).resolve().parents[1]\n",
    "ROOT = Path(__file__).resolve().parents[1]  # the planning repository\n"
    "# The product checkout the prompts send the implementer to (the code lives there, the plans here).\n"
    'PRODUCT = Path(os.environ.get("TT_PRODUCT_DIR") or ROOT.parent / "team_tracker").resolve()\n',
)
s = s.replace("import argparse\n", "import argparse\nimport os\n", 1)
s = re.sub(
    r"def repo_url\(\) -> str:\n.*?\n\n\n",
    '''def repo_url() -> str:
    """The PRODUCT repository (TT_PRODUCT_URL, else the git remote of TT_PRODUCT_DIR, else its path)."""
    if url := os.environ.get("TT_PRODUCT_URL"):
        return url
    try:
        return subprocess.run(
            ["git", "-C", str(PRODUCT), "remote", "get-url", "origin"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return str(PRODUCT)


def product_commit() -> str:
    """The product commit a prompt targets (HEAD of TT_PRODUCT_DIR), so each plan names the code it was written against."""
    try:
        return subprocess.run(
            ["git", "-C", str(PRODUCT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown (set TT_PRODUCT_DIR)"


''',
    s,
    count=1,
    flags=re.S,
)
s = s.replace(
    '"path/URL": url or repo_url(),',
    '"path/URL": f"{url or repo_url()} (product commit {product_commit()})",',
)
h.write_text(s)

old = repo / "apps" / "server" / "tests" / "unit" / "test_handover.py"
if old.exists():
    t = old.read_text().replace(
        "ROOT = Path(__file__).resolve().parents[4]", "ROOT = Path(__file__).resolve().parents[1]"
    )
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_handover.py").write_text(t)
    old.unlink()
    for d in [old.parent, old.parent.parent, old.parent.parent.parent, old.parent.parent.parent.parent]:
        if d.exists() and not any(d.iterdir()):
            d.rmdir()
print("handover tooling adapted: TT_PRODUCT_DIR =", "product checkout")
