"""Write docs/api/snapshot-schema.json and docs/api/presentation-schema.json from the Pydantic models (CI fails on drift)."""

import json
from pathlib import Path

from pydantic import BaseModel
from tt_server.domain.presentations import CONFIG, SLIDE_KINDS, TEMPLATES, SlideIn
from tt_server.domain.snapshot import json_schema

DOCS = Path(__file__).resolve().parents[1] / "docs" / "api"


class _Presentation(BaseModel):
    """Shape of `presentation` inside `GET /presentations/{id}/data`."""

    id: str
    title: str
    scope: str
    range_days: int
    weight_scheme_id: str | None
    theme: str
    slides: list[SlideIn]
    is_shared: bool


def presentation_schema() -> dict:
    s = _Presentation.model_json_schema()
    s["x-slide-kinds"] = {
        k: {"required_config": list(CONFIG[k][0]), "default_config": CONFIG[k][1]} for k in SLIDE_KINDS
    }
    s["x-templates"] = {k: [sl.kind for sl in v[1]] for k, v in TEMPLATES.items()}
    return s


def render() -> dict[str, str]:
    return {
        "snapshot-schema.json": json.dumps(json_schema(), indent=2, sort_keys=True) + "\n",
        "presentation-schema.json": json.dumps(presentation_schema(), indent=2, sort_keys=True) + "\n",
    }


if __name__ == "__main__":
    for name, text in render().items():
        (DOCS / name).write_text(text)
        print("wrote", DOCS / name)
