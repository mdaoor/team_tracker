"""Corrections (Tab-edit) recorded as (raw, field, old, new) for the opt-in feedback endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tt_schema.client import ClientMessage

TRACKED = (
    "action",
    "item",
    "project",
    "minutes",
    "planned_end",
    "estimate",
    "tags",
    "priority",
    "note",
    "stage",
)


def diff_messages(old: ClientMessage, new: ClientMessage) -> list[dict[str, Any]]:
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    return [
        {
            "client_id": new.client_id,
            "field": f,
            "raw": new.raw_input or old.raw_input,
            "previous": a.get(f),
            "corrected": b.get(f),
        }
        for f in TRACKED
        if a.get(f) != b.get(f)
    ]


def diff_batches(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    olds = {m["client_id"]: ClientMessage.model_validate(m) for m in old["messages"]}
    out: list[dict[str, Any]] = []
    for m in new["messages"]:
        nm = ClientMessage.model_validate(m)
        if nm.client_id in olds:
            out.extend(diff_messages(olds[nm.client_id], nm))
    return out


class FeedbackLog:
    """Local JSONL buffer; flushed to POST /capture/feedback when the user opted in."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            for i in items:
                fh.write(json.dumps(i, default=str) + "\n")

    def drain(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        self.path.unlink()
        return rows
