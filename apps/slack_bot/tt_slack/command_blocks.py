"""Block Kit for management commands (Phase 14): the command card, approval / waive buttons, and modal forms generated from the
catalogue's JSON Schema (via `tt_client.forms.fields_for`, the same spec the CLI and desktop render).

Everything is a plain dict so payloads can be snapshot-tested and posted by any Slack client."""

from __future__ import annotations

import json
from typing import Any

from tt_client.command_flow import Outcome
from tt_client.commands import strip_meta
from tt_client.forms import FieldSpec

ACTION_CONFIRM = "tt_cmd_confirm"
ACTION_EDIT = "tt_cmd_edit"
ACTION_DISCARD = "tt_cmd_discard"
ACTION_APPROVE = "tt_approve"
ACTION_REJECT = "tt_reject"
ACTION_WAIVE = "tt_waive"
COMMAND_ACTIONS = (ACTION_CONFIRM, ACTION_EDIT, ACTION_DISCARD, ACTION_APPROVE, ACTION_REJECT, ACTION_WAIVE)
MODAL_CALLBACK = "tt_cmd_form"
MAX_OPTIONS = 100


def _text(s: str, kind: str = "mrkdwn") -> dict[str, Any]:
    return {"type": kind, "text": s[:3000]}


def _value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def command_card(
    outcomes: list[Outcome], batch_id: str, *, role_label: str = "", permission: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """One block group per command: args, an "Effect preview" section, then Confirm / Edit / Discard.

    A destructive command's Confirm carries Slack's native confirmation dialog (the second confirmation) and a value flag the
    handler insists on; a command the server would reject has no Confirm button at all."""
    blocks: list[dict[str, Any]] = []
    any_rejected = any(o.rejected for o in outcomes)
    destructive = any(o.destructive for o in outcomes)
    for n, o in enumerate(outcomes):
        if n:
            blocks.append({"type": "divider"})
        c = o.command
        title = c.op + ("  :warning: destructive" if o.destructive else "")
        blocks.append({"type": "header", "text": _text(c.op, "plain_text")})
        if o.destructive:
            blocks.append(
                {
                    "type": "context",
                    "elements": [_text(":warning: destructive: this cannot be undone from Slack")],
                }
            )
        fields = [
            {"type": "mrkdwn", "text": f"*{k.replace('_', ' ')}*\n{_value(v)}"[:2000]}
            for k, v in strip_meta(c.args).items()
        ][:10]
        if c.on_behalf_of:
            fields = [*fields[:9], {"type": "mrkdwn", "text": f"*on behalf of*\n{c.on_behalf_of}"}]
        if fields:
            blocks.append({"type": "section", "fields": fields})
        blocks.append({"type": "section", "text": _text(_effect_text(o))})
        _ = title
    buttons: list[dict[str, Any]] = []
    if not any_rejected:
        confirm: dict[str, Any] = {
            "type": "button",
            "action_id": ACTION_CONFIRM,
            "style": "danger" if destructive else "primary",
            "text": _text("Confirm", "plain_text"),
            "value": json.dumps({"batch": batch_id, "second": destructive}),
        }
        if destructive:
            confirm["confirm"] = {
                "title": _text("Really do this?", "plain_text"),
                "text": _text("This is destructive and cannot be undone from the client."),
                "confirm": _text("Yes, do it", "plain_text"),
                "deny": _text("Cancel", "plain_text"),
            }
        buttons.append(confirm)
    buttons += [
        {"type": "button", "action_id": ACTION_EDIT, "text": _text("Edit", "plain_text"), "value": batch_id},
        {
            "type": "button",
            "action_id": ACTION_DISCARD,
            "text": _text("Discard", "plain_text"),
            "value": batch_id,
        },
    ]
    blocks.append({"type": "actions", "block_id": f"cmd:{batch_id}", "elements": buttons})
    foot = [role_label] if role_label else []
    foot += [f"{op} needs `{perm}`" for op, perm in (permission or {}).items()]
    if foot:
        blocks.append({"type": "context", "elements": [_text(" · ".join(foot))]})
    return blocks


def _effect_text(o: Outcome) -> str:
    head = "*Effect preview*\n"
    if o.status == "previewed":
        return head + ("\n".join(f"• *{r.label}* {r.value}" for r in o.rows) or "_no visible change_")
    if o.status == "rejected":
        return head + f":x: *rejected* {o.reason()}"
    if o.status == "offline":
        return head + "_offline: the server will validate this when it is sent_"
    return head + "_this op has no preview_"


def approval_message(approval: dict[str, Any]) -> list[dict[str, Any]]:
    """An approval request as a message: one click decides (the button IS the confirmation)."""
    aid = str(approval.get("id"))
    title = approval.get("title") or approval.get("summary") or ""
    return [
        {"type": "section", "text": _text(f"*Approval {aid}*\n{title}")},
        {
            "type": "actions",
            "block_id": f"approval:{aid}",
            "elements": [
                {"type": "button", "action_id": ACTION_APPROVE, "style": "primary", "text": _text("Approve", "plain_text"), "value": aid},
                {"type": "button", "action_id": ACTION_REJECT, "style": "danger", "text": _text("Reject", "plain_text"), "value": aid},
            ],
        },
    ]  # fmt: skip


def waive_message(violation: dict[str, Any]) -> list[dict[str, Any]]:
    """A violation with a Waive button; waiving needs a reason, so the button opens a one-field modal."""
    vid = str(violation.get("id"))
    title = violation.get("title") or violation.get("summary") or ""
    return [
        {"type": "section", "text": _text(f"*Violation {vid}*\n{title}")},
        {
            "type": "actions",
            "block_id": f"violation:{vid}",
            "elements": [
                {
                    "type": "button",
                    "action_id": ACTION_WAIVE,
                    "text": _text("Waive…", "plain_text"),
                    "value": vid,
                }
            ],
        },
    ]


# ------------------------------------------------------------------ modal forms from the catalogue
def _option(v: str, label: str = "") -> dict[str, Any]:
    return {"text": _text((label or v)[:75], "plain_text"), "value": v[:75]}


def _input(spec: FieldSpec) -> dict[str, Any]:
    name, multi = spec.name, spec.multi
    use_select = spec.choices and len(spec.choices) <= MAX_OPTIONS and spec.picker != "item"
    hint = ""
    if use_select:
        el: dict[str, Any] = {
            "type": "multi_static_select" if multi else "static_select",
            "action_id": name,
            "placeholder": _text("Choose…", "plain_text"),
            "options": [_option(c.value, c.label) for c in spec.choices],
        }
    elif spec.kind == "boolean":
        el = {"type": "checkboxes", "action_id": name, "options": [_option("true", spec.label)]}
    elif spec.kind == "date" and not multi:
        el = {"type": "datepicker", "action_id": name}
    else:
        el = {
            "type": "plain_text_input",
            "action_id": name,
            "multiline": spec.kind == "json" or name in ("note", "reason", "comment"),
        }
        if multi:
            hint = "comma separated"
        elif spec.kind == "json":
            hint = "JSON"
        if spec.picker == "item" and spec.choices:
            el["placeholder"] = _text("e.g. " + ", ".join(c.value for c in spec.choices[:3]), "plain_text")
    block: dict[str, Any] = {
        "type": "input",
        "block_id": name,
        "optional": not spec.required,
        "label": _text(spec.label[:2000], "plain_text"),
        "element": el,
    }
    if hint or spec.description:
        block["hint"] = _text(" · ".join(x for x in (spec.description, hint) if x)[:2000], "plain_text")
    return block


def command_modal(
    op: str, fields: list[FieldSpec], *, batch_id: str = "", answers: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The op's form, generated from its JSON Schema. Required fields are non-optional; Slack allows 100 blocks."""
    blocks = [_input(f) for f in fields][:100]
    for b in blocks:  # prefill (edit-from-card)
        v = (answers or {}).get(b["block_id"])
        if v in (None, "", []):
            continue
        el = b["element"]
        if el["type"] == "plain_text_input":
            el["initial_value"] = (
                ", ".join(map(str, v))
                if isinstance(v, list)
                else (json.dumps(v) if isinstance(v, dict) else str(v))
            )
        elif el["type"] == "datepicker":
            el["initial_date"] = str(v)
        elif el["type"] in ("static_select", "multi_static_select"):
            vals = v if isinstance(v, list) else [v]
            pick = [o for o in el["options"] if o["value"] in [str(x)[:75] for x in vals]]
            if pick:
                el["initial_options" if el["type"] == "multi_static_select" else "initial_option"] = (
                    pick if el["type"] == "multi_static_select" else pick[0]
                )
        elif el["type"] == "checkboxes" and v:
            el["initial_options"] = el["options"]
    return {
        "type": "modal",
        "callback_id": MODAL_CALLBACK,
        "private_metadata": json.dumps({"op": op, "batch": batch_id}),
        "title": _text(op[:24], "plain_text"),
        "submit": _text("Preview", "plain_text"),
        "close": _text("Cancel", "plain_text"),
        "blocks": blocks,
    }


def waive_modal(violation_id: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": MODAL_CALLBACK,
        "private_metadata": json.dumps({"op": "violation.waive", "batch": "", "fixed": {"id": violation_id}}),
        "title": _text("Waive " + violation_id, "plain_text"),
        "submit": _text("Preview", "plain_text"),
        "close": _text("Cancel", "plain_text"),
        "blocks": [
            {
                "type": "input",
                "block_id": "reason",
                "label": _text("Why is it waived?", "plain_text"),
                "element": {"type": "plain_text_input", "action_id": "reason", "multiline": True},
            }
        ],
    }


def command_modal_values(view_state: dict[str, Any]) -> dict[str, Any]:
    """Slack's `view.state.values` -> the flat answers `tt_client.forms.coerce` types from the schema."""
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
            elif t == "datepicker":
                out[name] = v.get("selected_date")
            elif t == "checkboxes":
                out[name] = bool(v.get("selected_options"))
    return out


def modal_errors(errors: dict[str, str]) -> dict[str, Any]:
    return {"response_action": "errors", "errors": errors}
