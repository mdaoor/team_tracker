"""Write the OpenAPI document: `python -m tt_server.openapi docs/api/openapi.json`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tt_server.main import create_app
from tt_server.settings import Settings


def build() -> dict[str, object]:
    return create_app(Settings(database_url="postgresql+psycopg://tt:tt@localhost/tt")).openapi()


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/api/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
