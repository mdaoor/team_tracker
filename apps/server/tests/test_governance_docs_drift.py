"""The committed governance reference docs match what `tools/gen_governance_docs.py` generates from the declarations."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.gen_governance_docs import schema_md, templates_md  # noqa: E402


def test_committed_governance_docs_are_in_sync() -> None:
    for name, text in (("context-schema-v1.md", schema_md()), ("templates.md", templates_md())):
        assert (ROOT / "docs/governance" / name).read_text().rstrip() == text.rstrip(), (
            f"{name} is stale: run `uv run python tools/gen_governance_docs.py`"
        )
