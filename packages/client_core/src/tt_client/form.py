"""Mode B (manual): a form whose choices come from the ContextPack, with client-side mandatory fields."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from tt_schema.base import new_id
from tt_schema.client import ClientAction, ClientBatch, ClientMessage, ClientTaskRef
from tt_schema.enums import ItemTypeKey, Priority

from tt_client.pack import ContextPack
from tt_client.timeparse import parse_duration, parse_when

FIELDS = [
    "action",
    "item",
    "project",
    "item_type",
    "minutes",
    "tags",
    "priority",
    "planned_end",
    "estimate",
    "assignees",
    "milestone",
    "iteration",
    "stage",
    "product",
    "risks",
    "note",
    "evidence",
    "on_behalf_of",
    "custom_fields",
    "jira",
]


def choices(pack: ContextPack) -> dict[str, list[str]]:
    """Exactly the lists the pack publishes; nothing else is offered."""
    return {
        "action": [a.value for a in ClientAction],
        "item": [f"{i['key']}  {i['title']}" for i in pack.open_items],
        "project": pack.project_keys,
        "item_type": [t.value for t in ItemTypeKey],
        "tags": pack.tags,
        "priority": [p.value for p in Priority],
        "assignees": pack.people_handles,
        "milestone": pack.milestone_names,
        "iteration": pack.iteration_names,
        "risks": pack.risk_keys,
        "stage": sorted({s for stages in pack.data.get("stages", {}).values() for s in stages}),
    }


def _present(answers: dict[str, Any], name: str) -> bool:
    v = answers.get(name)
    return v not in (None, "", [], {})


def missing_required(answers: dict[str, Any], pack: ContextPack) -> list[str]:
    required = list(pack.required_for(answers.get("project")))
    action = answers.get("action")
    if action == ClientAction.LOG_TIME.value and "minutes" not in required:
        required.append("minutes")
    if action == ClientAction.CREATE.value and "item_type" not in required:
        required.append("item_type")
    return [f for f in required if not _present(answers, f)]


def build_message(
    answers: dict[str, Any], *, actor: str, now: datetime, tz: str = "UTC", hours_per_day: int = 8
) -> ClientMessage:
    item_raw = (answers.get("item") or "").strip()
    ref = None
    if item_raw:
        key = re.match(r"^([A-Z][A-Z0-9]+-\d+)\b", item_raw)
        ref = ClientTaskRef(key=key.group(1)) if key else ClientTaskRef(title=item_raw)
    minutes = answers.get("minutes")
    if isinstance(minutes, str):
        minutes = parse_duration(minutes, hours_per_day)
    planned = answers.get("planned_end")
    if isinstance(planned, str) and planned:
        planned = parse_when(planned, now, tz)
    estimate = answers.get("estimate")
    if isinstance(estimate, str) and estimate:
        mt = re.match(r"^(\d+(?:\.\d+)?)\s*(d|h|pts?|points?)", estimate.strip(), re.I)
        estimate = (
            {
                "value": float(mt.group(1)),
                "unit": {"d": "days", "h": "hours"}.get(mt.group(2)[0].lower(), "points"),
            }
            if mt
            else None
        )
    evidence = [{"kind": "link", "url": u} for u in answers.get("evidence", []) or []]
    jira = None
    if answers.get("jira"):
        jira = {"project": answers.get("project"), "send": True}
    return ClientMessage(
        actor=actor,
        action=ClientAction(answers["action"]),
        at=now,
        client_id=new_id(),
        item=ref,
        project=answers.get("project") or None,
        tags=list(answers.get("tags") or []),
        note=answers.get("note") or None,
        minutes=minutes or None,
        planned_end=planned or None,
        estimate=estimate or None,
        evidence=evidence or None,
        on_behalf_of=answers.get("on_behalf_of") or None,
        item_type=ItemTypeKey(answers["item_type"]) if answers.get("item_type") else None,
        priority=Priority(answers["priority"]) if answers.get("priority") else None,
        milestone=answers.get("milestone") or None,
        iteration=answers.get("iteration") or None,
        stage=answers.get("stage") or None,
        product=answers.get("product") or None,
        risks=list(answers.get("risks") or []),
        assignees=[{"person": p, "role": "owner"} for p in answers.get("assignees") or []],
        custom_fields=dict(answers.get("custom_fields") or {}),
        jira=jira,
        raw_input=None,
    )


def to_batch(messages: list[ClientMessage], workspace: str) -> ClientBatch:
    return ClientBatch(workspace=workspace, messages=messages, mode="manual")


def message_to_answers(msg: ClientMessage) -> dict[str, Any]:
    """Inverse of build_message, so `tt form --from <id>` round-trips an assisted file field by field."""
    item = msg.item
    return {
        "action": msg.action.value,
        "item": (item.key or item.title or "") if item else "",
        "project": msg.project,
        "item_type": msg.item_type.value if msg.item_type else None,
        "minutes": msg.minutes,
        "tags": msg.tags,
        "priority": msg.priority.value if msg.priority else None,
        "planned_end": msg.planned_end.isoformat() if msg.planned_end else None,
        "estimate": f"{msg.estimate['value']}{msg.estimate['unit'][0]}" if msg.estimate else None,
        "assignees": [a["person"] for a in msg.assignees],
        "milestone": msg.milestone,
        "iteration": msg.iteration,
        "stage": msg.stage,
        "product": msg.product,
        "risks": msg.risks,
        "note": msg.note,
        "evidence": [e.get("url") for e in msg.evidence or [] if e.get("url")],
        "on_behalf_of": msg.on_behalf_of,
        "custom_fields": msg.custom_fields,
        "jira": bool(msg.jira and msg.jira.get("send")),
    }
