"""Endpoints the manager console needs beyond plain CRUD (Phase 5): live review queue, rules dry-run, stage reorder."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.enums import TargetType

from tt_server.acl.visibility import redact_event, visible
from tt_server.api.authz import needs, needs_review
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _item, row_dict
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.contextpack import build_pack
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import review as review_svc
from tt_server.integrations.llm import GatewayUnavailable

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["console"])
POLL_SECONDS = 2.0


# ---------------------------------------------------------------- live review queue (SSE, ADR-0009)
def _digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha1(json.dumps([r["ref"] for r in rows]).encode(), usedforsecurity=False).hexdigest()  # noqa: S324


@router.get("/review-queue/stream", dependencies=[needs_review()])
async def review_stream(request: Request, actor: CurrentActor, once: bool = False) -> StreamingResponse:
    """Server-sent events: `event: queue` with the open refs whenever they change (polled server-side every 2 s).

    The actor's ACL context is captured once; a long-lived stream therefore reflects the permissions it was opened with.
    """
    factory = request.app.state.session_factory
    ws_id, person_id = actor.ws.id, actor.person.id

    async def gen() -> AsyncIterator[str]:
        last = ""
        while True:
            async with factory() as s:
                rows = await review_svc.list_queue(s, ws_id, person_id)
            d = _digest(rows)
            if d != last:
                last = d
                yield f"event: queue\ndata: {json.dumps({'count': len(rows), 'refs': [r['ref'] for r in rows]})}\n\n"
            else:
                yield ": keep-alive\n\n"
            if once or await request.is_disconnected():
                return
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- translator rules dry-run
class DryRunIn(BaseModel):
    rules: list[str] = Field(max_length=50)
    sample: int = Field(default=200, ge=1, le=200)


@router.post("/translator-rules:dry-run", dependencies=[needs("translator_rule.write")])
async def rules_dry_run(body: DryRunIn, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Which of the last N captured lines would parse differently under `rules`? Needs the LLM gateway."""
    stmt = visible(
        select(m.Event).where(m.Event.workspace_id == actor.ws.id, m.Event.raw_input.is_not(None)),
        actor.acl,
        m.Event,
    )
    events = (
        await uow.session.scalars(stmt.order_by(m.Event.occurred_at.desc()).limit(body.sample * 3))
    ).all()
    lines: list[str] = []
    for e in events:  # raw_input is private capture: only lines the actor may read are used
        if redact_event(actor.acl, row_dict(e)).get("raw_input") and e.raw_input not in lines:
            lines.append(str(e.raw_input))
        if len(lines) >= body.sample:
            break
    client = request.app.state.llm
    if client is None:
        return {
            "available": False,
            "reason": "the LLM gateway is not configured",
            "checked": 0,
            "changed": 0,
            "results": [],
        }
    pack = await build_pack(uow.session, actor.ws, actor.member)
    try:
        ans = await client.rules_dry_run(actor.ws.slug, lines, body.rules, pack, actor.member.handle)
    except GatewayUnavailable as exc:
        return {"available": False, "reason": str(exc), "checked": 0, "changed": 0, "results": []}
    out = ans.output or {}
    return {
        "available": True,
        "checked": out.get("checked", 0),
        "changed": out.get("changed", 0),
        "results": out.get("results", []),
    }


# ---------------------------------------------------------------- stage reorder
class ReorderIn(BaseModel):
    order: list[str] = Field(min_length=1, description="stage keys in the new order")


@router.patch("/processes/{ref}/stages:reorder", dependencies=[needs("process.admin")])
async def reorder_stages(ref: str, body: ReorderIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await proc.get_process(uow.session, actor.ws.id, ref)
    stages = await proc.stages_of(uow.session, p.id)
    keys = [s.key for s in stages]
    if sorted(keys) != sorted(body.order) or len(set(body.order)) != len(body.order):
        raise ApiError(
            400, "invalid_order", "order must list every stage of the process exactly once", pointer="/order"
        )
    by_key = {s.key: s for s in stages}
    uow.context["actor_id"] = actor.person.id
    for i, k in enumerate(body.order):
        by_key[k].position = i
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="stage.reordered",
        payload={"before": keys, "after": body.order, "process": p.key},
    )
    await uow.commit()
    return {"process": p.key, "stages": [row_dict(by_key[k]) for k in body.order]}


# ---------------------------------------------------------------- audit trail of one item
@router.get("/items/{id_or_key}/events")
async def item_events(
    id_or_key: str, actor: CurrentActor, uow: Uow, limit: int = Query(100, ge=1, le=1000)
) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    stmt = visible(
        select(m.Event).where(m.Event.workspace_id == actor.ws.id, m.Event.entity_id == item.id),
        actor.acl,
        m.Event,
    )
    rows = (await uow.session.scalars(stmt.order_by(m.Event.occurred_at.desc()).limit(min(limit, 500)))).all()
    return {"data": [redact_event(actor.acl, row_dict(e)) for e in rows], "next_cursor": None}


# ---------------------------------------------------------------- workspaces of the signed-in person
_CAN = (
    "policy.read", "policy.write", "policy.admin", "violation.resolve", "violation.waive", "habit.coach",
    "habit.view_team", "weight.set", "dailylog.review", "project.create", "risk.write",
    "translator_rule.write", "classification.write", "project_member.write", "review_queue.resolve",
)  # fmt: skip


def nav_access(actor: Any) -> dict[str, bool]:
    """Which console areas the viewer may open. The web hides the rest and explains a forbidden deep link (UX 12)."""
    a = actor.acl
    can = a.can
    return {
        "setup": bool(
            a.is_admin or can("project.create") or can("risk.write") or can("translator_rule.write")
            or can("classification.write") or can("project_member.write")
        ),
        "review": bool(can("review_queue.resolve") or a.managed_ids),
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


@router.get("/me")
async def me(actor: CurrentActor) -> dict[str, Any]:
    return {
        "person_id": actor.person.id,
        "handle": actor.member.handle,
        "display_name": actor.person.display_name,
        "workspace": {"slug": actor.ws.slug, "name": actor.ws.name},
        "is_admin": actor.acl.is_admin,
        "can_review": bool(actor.acl.can("review_queue.resolve") or actor.acl.managed_ids),
        "can": {p: bool(actor.acl.can(p)) for p in _CAN},
        "nav": nav_access(actor),
        "settings": {"external_share": bool((actor.ws.settings or {}).get("external_share"))},
    }


# ---------------------------------------------------------------- workspace settings (admin)
class SettingsPatch(BaseModel):
    external_share: bool | None = None
    external_share_redaction: str | None = Field(default=None, pattern="^(standard|strict)$")


@router.get("/settings", dependencies=[needs("workspace.admin")])
async def get_settings(actor: CurrentActor) -> dict[str, Any]:
    s = actor.ws.settings or {}
    return {
        "external_share": bool(s.get("external_share")),
        "external_share_redaction": s.get("external_share_redaction", "standard"),
    }


@router.patch("/settings", dependencies=[needs("workspace.admin")])
async def patch_settings(body: SettingsPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Admin-only workspace switches the console needs (external sharing). One event per change."""
    ws = await uow.session.get(m.Workspace, actor.ws.id)
    assert ws is not None
    before = dict(ws.settings or {})
    after = {**before, **body.model_dump(exclude_none=True)}
    if after != before:
        ws.settings = after
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=actor.person.id,
            entity_type=TargetType.PERSON,
            entity_id=actor.person.id,
            event_type="workspace.settings_changed",
            payload={"before": {k: before.get(k) for k in body.model_dump(exclude_none=True)}, "after": body.model_dump(exclude_none=True)},
        )
        await uow.commit()
    return {
        "external_share": bool(after.get("external_share")),
        "external_share_redaction": after.get("external_share_redaction", "standard"),
    }
