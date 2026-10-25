"""Block Kit payloads: the parsed card, the Manual modal (built from the pack, same mandatory rules as the CLI form), small messages.

Everything is a plain dict so the payloads can be snapshot-tested and posted by any Slack client."""

from __future__ import annotations

import json
from typing import Any

from tt_client.form import choices
from tt_client.pack import ContextPack

ACTION_SEND = "tt_send"
ACTION_EDIT = "tt_edit"
ACTION_DISCARD = "tt_discard"
ACTION_MANUAL = "tt_manual"
ACTION_JIRA = "tt_jira"
MODAL_CALLBACK = "tt_manual_form"
MAX_OPTIONS = 100  # Slack's limit for a static select


def _text(s: str, kind: str = "mrkdwn") -> dict[str, Any]:
    return {"type": kind, "text": s[:3000]}


def card(
    cards: list[dict[str, Any]], batch_id: str, *, jira: bool | None = None, source: str = "structured"
) -> list[dict[str, Any]]:
    """One section per preview row (`{label, value, cue}`), buttons Send · Edit · Discard · Manual…

    `jira` is None when no project in the batch has Jira (no chip), else the current send flag."""
    blocks: list[dict[str, Any]] = []
    for n, c in enumerate(cards):
        if n:
            blocks.append({"type": "divider"})
        blocks.append({"type": "header", "text": _text(c["action"].replace("_", " ").title(), "plain_text")})
        for r in c["rows"]:
            cue = f"  _{r['cue']}_" if r.get("cue") else ""
            blocks.append({"type": "section", "text": _text(f"*{r['label']}*  {r['value']}{cue}")})
    buttons: list[dict[str, Any]] = [
        {"type": "button", "action_id": ACTION_SEND, "style": "primary", "text": _text("Send", "plain_text"), "value": batch_id},
        {"type": "button", "action_id": ACTION_EDIT, "text": _text("Edit", "plain_text"), "value": batch_id},
        {"type": "button", "action_id": ACTION_DISCARD, "style": "danger", "text": _text("Discard", "plain_text"), "value": batch_id},
        {"type": "button", "action_id": ACTION_MANUAL, "text": _text("Manual…", "plain_text"), "value": batch_id},
    ]  # fmt: skip
    if jira is not None:
        buttons.insert(
            1,
            {
                "type": "button", "action_id": ACTION_JIRA, "text": _text("Jira: on" if jira else "Jira: off", "plain_text"),
                "value": json.dumps({"batch": batch_id, "on": not jira}),
            },
        )  # fmt: skip
    blocks.append({"type": "actions", "block_id": f"card:{batch_id}", "elements": buttons})
    if source == "structured":
        blocks.append(
            {"type": "context", "elements": [_text("_Parsed without the model - check the rows._")]}
        )
    return blocks


def _option(v: str) -> dict[str, Any]:
    return {"text": _text(v[:75], "plain_text"), "value": v[:75]}


def _select(
    name: str,
    label: str,
    values: list[str],
    *,
    multi: bool = False,
    optional: bool = True,
    initial: Any = None,
) -> dict[str, Any]:
    opts = [_option(v) for v in values[:MAX_OPTIONS]]
    el: dict[str, Any] = {
        "type": "multi_static_select" if multi else "static_select", "action_id": name,
        "placeholder": _text("Choose…", "plain_text"), "options": opts,
    }  # fmt: skip
    if initial:
        pick = [o for o in opts if o["value"] in (initial if isinstance(initial, list) else [initial])]
        if pick:
            el["initial_options" if multi else "initial_option"] = pick if multi else pick[0]
    return {
        "type": "input",
        "block_id": name,
        "optional": optional,
        "label": _text(label, "plain_text"),
        "element": el,
    }


def _plain(
    name: str, label: str, *, optional: bool = True, initial: str = "", multiline: bool = False
) -> dict[str, Any]:
    el: dict[str, Any] = {"type": "plain_text_input", "action_id": name, "multiline": multiline}
    if initial:
        el["initial_value"] = initial
    return {
        "type": "input",
        "block_id": name,
        "optional": optional,
        "label": _text(label, "plain_text"),
        "element": el,
    }


def manual_modal(
    pack: ContextPack, answers: dict[str, Any] | None = None, *, batch_id: str = ""
) -> dict[str, Any]:
    """The Manual form. Every choice list is exactly what the pack publishes; mandatory fields are marked non-optional."""
    a = answers or {}
    ch = choices(pack)
    required = set(pack.required_for(a.get("project")))
    fields = [
        _select("action", "Action", ch["action"], optional=False, initial=a.get("action")),
        _select("project", "Project", ch["project"], optional="project" not in required, initial=a.get("project")),
        _select("item", "Task", ch["item"], optional="item" not in required),
        _plain("minutes", "Time (e.g. 45m, 2h)", optional="minutes" not in required, initial=str(a.get("minutes") or "")),
        _select("tags", "Tags", ch["tags"], multi=True, optional="tags" not in required, initial=a.get("tags")),
        _select("priority", "Priority", ch["priority"], optional="priority" not in required, initial=a.get("priority")),
        _plain("planned_end", "Plan: ends (e.g. tomorrow 5pm)", optional="planned_end" not in required, initial=str(a.get("planned_end") or "")),
        _plain("note", "Note", optional="note" not in required, initial=a.get("note") or "", multiline=True),
    ]  # fmt: skip
    return {
        "type": "modal", "callback_id": MODAL_CALLBACK, "private_metadata": batch_id,
        "title": _text("Team Tracker", "plain_text"), "submit": _text("Add to card", "plain_text"),
        "close": _text("Cancel", "plain_text"), "blocks": fields,
    }  # fmt: skip


def modal_values(view_state: dict[str, Any]) -> dict[str, Any]:
    """Slack's `view.state.values` -> the flat answers dict `tt_client.form.build_message` expects."""
    out: dict[str, Any] = {}
    for block in view_state.get("values", {}).values():
        for name, v in block.items():
            t = v.get("type")
            if t == "plain_text_input":
                out[name] = v.get("value") or ""
            elif t == "static_select":
                out[name] = (v.get("selected_option") or {}).get("value")
            elif t == "multi_static_select":
                out[name] = [o["value"] for o in v.get("selected_options") or []]
    if isinstance(out.get("item"), str) and out["item"]:
        out["item"] = out["item"].split("  ", 1)[0]  # the choice is "KEY  title": keep the key
    return out


def modal_errors(missing: list[str]) -> dict[str, Any]:
    """`response_action: errors` payload naming each missing mandatory field next to its input."""
    return {
        "response_action": "errors",
        "errors": {f: "This field is required by your workspace" for f in missing},
    }


def text_message(text: str, *, ephemeral: bool = False) -> dict[str, Any]:
    return {"text": text, "response_type": "ephemeral" if ephemeral else "in_channel"}


def result_message(results: list[dict[str, Any]]) -> str:
    lines = []
    for r in results:
        flag = {"applied": ":white_check_mark:", "deduplicated": ":white_check_mark: (already had it)",
                "applied_with_warnings": ":warning:"}.get(r.get("status", ""), ":x:")  # fmt: skip
        item = r.get("item") or {}
        extra = f" · Jira {r['jira']['key']}" if (r.get("jira") or {}).get("key") else ""
        lines.append(f"{flag} {item.get('key', '')} {item.get('title', '')}{extra}".strip())
        lines.extend(f"    :small_orange_diamond: {w}" for w in r.get("warnings", []))
        if r.get("review_item_ids"):
            lines.append("    :arrow_right: sent to your manager's review queue")
        if r.get("status") == "failed":
            lines.append(f"    error: {r.get('error_code')}")
    return "\n".join(lines) or "Nothing was applied."
