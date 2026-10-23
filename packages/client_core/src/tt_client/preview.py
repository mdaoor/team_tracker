"""Preview rows for the capture card (UX §2.2) with confidence cues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tt_schema.client import ClientMessage

from tt_client.pack import ContextPack


@dataclass
class Row:
    label: str
    value: str
    cue: str = ""


def _cue(msg: ClientMessage, field: str, matched: bool) -> str:
    if matched:
        return "✓ matched"
    conf = (msg.llm_confidence or {}).get(field)
    if conf is not None:
        return f"{round(conf * 100)} %"
    return ""


def build_preview(msg: ClientMessage, pack: ContextPack | None) -> list[Row]:
    rows: list[Row] = []
    by_key = {i["key"]: i for i in (pack.open_items if pack else [])}
    item = msg.item
    if item and item.key:
        known = by_key.get(item.key)
        rows.append(
            Row(
                "Task",
                f"{known['title'] if known else (item.title or '')} {item.key}".strip(),
                "✓ matched" if known else "",
            )
        )
    elif item and item.title:
        match = next(
            (i for i in (pack.open_items if pack else []) if i["title"].lower() == item.title.lower()), None
        )
        rows.append(
            Row(
                "Task",
                item.title + (f"  {match['key']}" if match else ""),
                _cue(msg, "item", match is not None) or "new",
            )
        )
    elif msg.note:
        rows.append(Row("Note", msg.note))
    else:
        rows.append(Row("Task", "(current activity)"))
    if msg.project:
        key = pack.project_by_alias(msg.project) if pack else None
        rows.append(
            Row(
                "Project",
                f"{msg.project}" + (f" ({key})" if key else ""),
                _cue(msg, "project", key is not None),
            )
        )
    else:
        rows.append(Row("Project", "—", "will be sorted by your manager"))
    if msg.minutes:
        h, m = divmod(msg.minutes, 60)
        rows.append(
            Row(
                "Time", " ".join(x for x in (f"{h} h" if h else "", f"{m} min" if m else "") if x) + " logged"
            )
        )
    if msg.planned_end:
        rows.append(Row("Plan", "ends " + msg.planned_end.strftime("%a %d %b %H:%M")))
    if msg.estimate:
        rows.append(Row("Estimate", f"{msg.estimate.get('value')} {msg.estimate.get('unit')}"))
    if msg.tags:
        known_tags = set(pack.tags) if pack else set()
        rows.append(
            Row(
                "Tags",
                "  ".join("#" + t for t in msg.tags),
                "✓ matched" if set(msg.tags) <= known_tags else "suggested",
            )
        )
    if msg.stage:
        rows.append(Row("Stage", msg.stage, _cue(msg, "stage", False) or "ask"))
    if msg.priority:
        rows.append(Row("Priority", msg.priority.value, _cue(msg, "priority", False)))
    for ev in msg.evidence or []:
        rows.append(Row("Evidence", f"{ev.get('kind', 'link')} {ev.get('url') or ev.get('title') or ''}"))
    return rows


def preview_dict(msgs: list[ClientMessage], pack: ContextPack | None) -> dict[str, Any]:
    return {
        "cards": [
            {"action": m.action.value, "rows": [r.__dict__ for r in build_preview(m, pack)]} for m in msgs
        ]
    }
