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

from tt_server.acl.modes import modes_for, role_keys
from tt_server.api.authz import needs, needs_review
from tt_server.api.deps import CurrentActor, Uow
from tt_server.domain import console as svc
from tt_server.domain.overlay import review as review_svc

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
    return await svc.rules_dry_run(
        uow, actor.ws, actor.member, actor.acl, request.app.state.llm, rules=body.rules, sample=body.sample
    )


# ---------------------------------------------------------------- stage reorder
class ReorderIn(BaseModel):
    order: list[str] = Field(min_length=1, description="stage keys in the new order")


@router.patch("/processes/{ref}/stages:reorder", dependencies=[needs("process.admin")])
async def reorder_stages(ref: str, body: ReorderIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    out = await svc.reorder_stages(uow, actor.ws.id, actor.person.id, ref, body.order)
    await uow.commit()
    return out


# ---------------------------------------------------------------- audit trail of one item
@router.get("/items/{id_or_key}/events")
async def item_events(
    id_or_key: str, actor: CurrentActor, uow: Uow, limit: int = Query(100, ge=1, le=1000)
) -> dict[str, Any]:
    return {"data": await svc.item_events(uow, actor.ws.id, actor.acl, id_or_key, limit), "next_cursor": None}


# ---------------------------------------------------------------- workspaces of the signed-in person
@router.get("/me")
async def me(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {
        "person_id": actor.person.id,
        "handle": actor.member.handle,
        "display_name": actor.person.display_name,
        "workspace": {"slug": actor.ws.slug, "name": actor.ws.name},
        "is_admin": actor.acl.is_admin,
        "can_review": bool(actor.acl.can("review_queue.resolve") or actor.acl.managed_ids),
        "can": {p: bool(actor.acl.can(p)) for p in svc.CAN_FLAGS},
        "nav": svc.nav_access(actor.acl),
        "roles": role_keys(actor.acl),
        "role_modes": await modes_for(uow.session, actor.acl),
        "settings": {"external_share": bool((actor.ws.settings or {}).get("external_share"))},
    }


# ---------------------------------------------------------------- workspace settings (admin)
class SettingsPatch(BaseModel):
    external_share: bool | None = None
    external_share_redaction: str | None = Field(default=None, pattern="^(standard|strict)$")


@router.get("/settings", dependencies=[needs("workspace.admin")])
async def get_settings(actor: CurrentActor) -> dict[str, Any]:
    return svc.settings_view(actor.ws.settings)


@router.patch("/settings", dependencies=[needs("workspace.admin")])
async def patch_settings(body: SettingsPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Admin-only workspace switches the console needs (external sharing). One event per change."""
    view, changed = await svc.update_settings(
        uow, actor.ws.id, actor.person.id, body.model_dump(exclude_none=True)
    )
    if changed:
        await uow.commit()
    return view
