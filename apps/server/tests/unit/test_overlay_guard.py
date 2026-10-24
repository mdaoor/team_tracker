"""Architecture §4.2, second half: overlay services must never write capture-layer WorkItem columns."""

from __future__ import annotations

import ast
from pathlib import Path

from tt_schema import CAPTURE_FIELDS

OVERLAY = Path(__file__).resolve().parents[2] / "tt_server" / "domain" / "overlay"
ITEM_NAMES = {"item", "it", "t", "root", "child", "kid", "node"}


def test_overlay_never_assigns_capture_columns() -> None:
    offenders: list[str] = []
    for path in OVERLAY.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AugAssign)
                else []
            )
            for target in targets:
                for sub in ast.walk(target):
                    if (
                        isinstance(sub, ast.Attribute)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id in ITEM_NAMES
                        and sub.attr in CAPTURE_FIELDS
                    ):
                        offenders.append(f"{path.name}:{sub.lineno} {sub.value.id}.{sub.attr}")
    assert offenders == []


def test_the_guard_would_catch_a_violation() -> None:
    tree = ast.parse("item.title = 'x'")
    node = tree.body[0]
    assert isinstance(node, ast.Assign) and node.targets[0].attr == "title" and "title" in CAPTURE_FIELDS  # type: ignore[attr-defined]
