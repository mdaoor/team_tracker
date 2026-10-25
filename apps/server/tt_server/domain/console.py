"""Logic behind the manager-console endpoints: rules dry-run, stage reorder, item audit trail, workspace settings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.enums import TargetType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import redact_event, visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, row_dict
from tt_server.domain.contextpack import build_pack
from tt_server.domain.errors import Invalid
from tt_server.domain.lookup import visible_item
from tt_server.domain.overlay import process as proc
from tt_server.integrations.llm import GatewayUnavailable

# Permissions the `/me` payload reports so the web can show or hide actions.
CAN_FLAGS = (
    "policy.read", "policy.write", "policy.admin", "violation.resolve", "violation.waive", "habit.coach",
    "habit.view_team", "weight.set", "dailylog.review", "project.create", "risk.write",
    "translator_rule.write", "classification.write", "project_member.write", "review_queue.resolve",
)  # fmt: skip

_NO_ANSWER = {"checked": 0, "changed": 0, "results": []}


async def _readable_lines(uow: UnitOfWork, ws_id: str, acl: AclContext, sample: int) -> list[str]:
    """Distinct recent raw capture lines the actor may read (raw_input is private capture)."""
    stmt = visible(
        select(m.Event).where(m.Event.workspace_id == ws_id, m.Event.raw_input.is_not(None)), acl, m.Event
    )
    events = (await uow.session.scalars(stmt.order_by(m.Event.occurred_at.desc()).limit(sample * 3))).all()
    lines: list[str] = []
    for e in events:
        if redact_event(acl, row_dict(e)).get("raw_input") and e.raw_input not in lines:
            lines.append(str(e.raw_input))
        if len(lines) >= sample:
            break
    return lines


async def rules_dry_run(
    uow: UnitOfWork,
    ws: m.Workspace,
    member: m.WorkspaceMember,
    acl: AclContext,
    client: Any,
    *,
    rules: list[str],
    sample: int,
) -> dict[str, Any]:
    """Which of the last N captured lines would parse differently under `rules`? Needs the LLM gateway."""
    lines = await _readable_lines(uow, ws.id, acl, sample)
    if client is None:
        return {"available": False, "reason": "the LLM gateway is not configured", **_NO_ANSWER}
    pack = await build_pack(uow.session, ws, member)
    try:
        ans = await client.rules_dry_run(ws.slug, lines, rules, pack, member.handle)
    except GatewayUnavailable as exc:
        return {"available": False, "reason": str(exc), **_NO_ANSWER}
    out = ans.output or {}
    return {
        "available": True,
        "checked": out.get("checked", 0),
        "changed": out.get("changed", 0),
        "results": out.get("results", []),
    }


async def reorder_stages(
    uow: UnitOfWork, ws_id: str, actor_id: str, ref: str, order: list[str]
) -> dict[str, Any]:
    """Renumber a process' stages to `order` (every stage exactly once) and record the event."""
    p = await proc.get_process(uow.session, ws_id, ref)
    stages = await proc.stages_of(uow.session, p.id)
    keys = [s.key for s in stages]
    if sorted(keys) != sorted(order) or len(set(order)) != len(order):
        raise Invalid(
            "invalid_order", "order must list every stage of the process exactly once", pointer="/order"
        )
    by_key = {s.key: s for s in stages}
    uow.context["actor_id"] = actor_id
    for i, k in enumerate(order):
        by_key[k].position = i
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=actor_id,
        event_type="stage.reordered",
        payload={"before": keys, "after": order, "process": p.key},
    )
    return {"process": p.key, "stages": [row_dict(by_key[k]) for k in order]}


async def item_events(
    uow: UnitOfWork, ws_id: str, acl: AclContext, id_or_key: str, limit: int
) -> list[dict[str, Any]]:
    """The visibility-filtered audit trail of one item, newest first."""
    item = await visible_item(uow, ws_id, id_or_key)
    stmt = visible(
        select(m.Event).where(m.Event.workspace_id == ws_id, m.Event.entity_id == item.id), acl, m.Event
    )
    rows = (await uow.session.scalars(stmt.order_by(m.Event.occurred_at.desc()).limit(min(limit, 500)))).all()
    return [redact_event(acl, row_dict(e)) for e in rows]


def nav_access(acl: AclContext) -> dict[str, bool]:
    """Which console areas the viewer may open. The web hides the rest and explains a forbidden deep link (UX 12)."""
    can = acl.can
    return {
        "setup": bool(
            acl.is_admin or can("project.create") or can("risk.write") or can("translator_rule.write")
            or can("classification.write") or can("project_member.write")
        ),
        "review": bool(can("review_queue.resolve") or acl.managed_ids),
        "views": True,
        "plan": True,
        "present": True,
        "policies": bool(can("policy.read")),
        "violations": bool(can("policy.read") or can("violation.resolve")),
        "habits": True,
        "performance": True,
        "digest": bool(can("dailylog.review")),
        "insights": True,
        "weights": bool(can("weight.set")),
    }  # fmt: skip


def settings_view(settings: dict[str, Any] | None) -> dict[str, Any]:
    """The admin-editable workspace switches, with defaults filled in."""
    s = settings or {}
    return {
        "external_share": bool(s.get("external_share")),
        "external_share_redaction": s.get("external_share_redaction", "standard"),
    }


async def update_settings(
    uow: UnitOfWork, ws_id: str, actor_id: str, changes: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Apply workspace setting changes; returns the new view and whether anything changed (one event per change)."""
    ws = await uow.session.get(m.Workspace, ws_id)
    assert ws is not None  # noqa: S101
    before = dict(ws.settings or {})
    after = {**before, **changes}
    if after == before:
        return settings_view(after), False
    ws.settings = after
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=actor_id,
        event_type="workspace.settings_changed",
        payload={"before": {k: before.get(k) for k in changes}, "after": changes},
    )
    return settings_view(after), True
