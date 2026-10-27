"""Regenerates the catalogue table in docs/client/management-commands.md and the client test fixture from the server registry.

Usage: `uv run python tools/gen_command_docs.py`
"""

from __future__ import annotations

import json
from pathlib import Path

from tt_server.api.commands import catalogue

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/client/management-commands.md"
FIXTURE = ROOT / "packages/client_core/tests/fixtures/catalogue.json"
HEADER = "| op | group | permission | flags | what it does |\n|---|---|---|---|---|\n"


def main() -> None:
    cat = catalogue()
    rows = []
    for e in cat["ops"]:
        flags = (
            ", ".join(f for f, v in (("preview", e["preview"]), ("destructive", e["destructive"]), ("admin token", e["token_scope"] == "admin")) if v)
            or "–"
        )
        rows.append(
            f"| `{e['op']}` | {e['group']} | {e['permission'] or 'any member'} | {flags} | {e['description']} |"
        )
    text = DOC.read_text()
    head = text[: text.rindex(HEADER)]
    DOC.write_text(head + HEADER + "\n".join(rows) + "\n")
    FIXTURE.write_text(json.dumps(cat, indent=1, default=str) + "\n")


if __name__ == "__main__":
    main()
