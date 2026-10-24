"""The plan-only rule is declared twice (Python for the builder and the catalogue, JS for the deck): they must stay identical."""

from __future__ import annotations

import re
from pathlib import Path

from tt_server.domain.presentations import CONFIG, KIND_META, SLIDE_KINDS

JS = Path(__file__).resolve().parents[4] / "apps/presentation/src/renderers/index.js"


def _js_meta() -> dict[str, dict[str, str]]:
    block = JS.read_text().split("export const KIND_META = {", 1)[1].split("}; // prettier-ignore", 1)[0]
    return {
        k: {"requires": r, "render_empty": e}
        for k, r, e in re.findall(
            r"(\w+):\s*\{\s*requires:\s*\"(\w+)\",\s*render_empty:\s*\"(\w+)\"\s*\}", block
        )
    }


def test_every_kind_declares_requires_and_render_empty() -> None:
    assert set(KIND_META) == set(SLIDE_KINDS) == set(CONFIG)
    for k, v in KIND_META.items():
        assert v["requires"] in {"plan", "activity", "both"} and v["render_empty"] in {
            "hide",
            "placeholder",
            "note",
        }, k


def test_python_and_js_declarations_are_identical() -> None:
    assert _js_meta() == KIND_META
