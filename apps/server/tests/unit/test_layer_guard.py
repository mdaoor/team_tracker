"""Architecture §4.2: the capture path writes only capture-layer (or system/either) WorkItem columns.

Overlay hints (priority, assignee, milestone...) are allowed only inside `apply_hints`, which records them
with source=rule / review items; nothing else in the ingest module may assign an overlay column.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tt_schema.items import WorkItem

INGEST = Path(__file__).resolve().parents[2] / "tt_server" / "domain" / "capture" / "ingest.py"
OVERLAY = {
    n for n, f in WorkItem.model_fields.items() if (f.json_schema_extra or {}).get("layer") == "overlay"
}
ITEM_NAMES = {"item", "it"}


def _assigned_attrs(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(fn):
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AugAssign)
            else []
        )
        for t in targets:
            for sub in ast.walk(t):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id in ITEM_NAMES
                ):
                    out.add(sub.attr)
    return out


def test_capture_path_does_not_write_overlay_columns_outside_hints() -> None:
    tree = ast.parse(INGEST.read_text())
    offenders: dict[str, set[str]] = {}
    for fn in [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)]:
        if fn.name == "apply_hints":
            continue
        bad = _assigned_attrs(fn) & OVERLAY
        if bad:
            offenders[fn.name] = bad
    assert offenders == {}


def test_overlay_columns_are_known() -> None:
    assert {"priority", "assignee_id", "milestone_id", "description"} <= OVERLAY
