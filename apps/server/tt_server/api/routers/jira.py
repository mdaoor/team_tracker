"""Phase 10 API: Jira connection, per-project mapping, manual push/link, sync log and the signed inbound webhook (ADR-0015).

Thin HTTP layer: parse, authorise, call `integrations.jira.admin`, commit.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from tt_server.api.authz import check_project, needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _project
from tt_server.integrations.jira import admin as svc
from tt_server.integrations.jira import inbound as inboundmod

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["jira"])
hook = APIRouter(prefix="/api/v1/integrations/jira", tags=["jira"])


class ConnIn(BaseModel):
    base_url: str = Field(pattern=r"^https://[^\s/]+(/.*)?$")
    email: str
    api_token: str | None = Field(default=None, min_length=4)
    enabled: bool = True


@router.get("/integrations/jira", dependencies=[needs("role.admin")])
async def get_connection(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return svc.connection_view(await svc.get_connection(uow, actor.ws.id, need=False), actor.ws.slug)


@router.put("/integrations/jira", dependencies=[needs("role.admin")])
async def put_connection(body: ConnIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    c, secret = await svc.save_connection(
        uow,
        actor.ws.id,
        actor.person.id,
        base_url=body.base_url,
        email=body.email,
        api_token=body.api_token,
        enabled=body.enabled,
    )
    await uow.commit()
    return svc.connection_view(c, actor.ws.slug, secret)


@router.post("/integrations/jira:rotate-secret", dependencies=[needs("role.admin")])
async def rotate_secret(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    c, secret = await svc.rotate_secret(uow, actor.ws.id, actor.person.id)
    await uow.commit()
    return svc.connection_view(c, actor.ws.slug, secret)


@router.post("/integrations/jira:test", dependencies=[needs("role.admin")])
async def test_connection(actor: CurrentActor, uow: Uow, project: str | None = None) -> dict[str, Any]:
    """Authenticate, learn the integration user (its edits are never echoed back), and optionally fetch a project's options."""
    result = await svc.check_connection(uow, actor.ws.id, actor.person.id, project)
    if result["ok"]:
        await uow.commit()
    return result


@router.get("/integrations/jira/log", dependencies=[needs("role.admin")])
async def sync_log(
    actor: CurrentActor, uow: Uow, limit: int = Query(50, ge=1, le=1000), item: str | None = None
) -> dict[str, Any]:
    return {"data": await svc.sync_log(uow, actor.ws.id, limit=limit, item=item), "next_cursor": None}


# ---------------------------------------------------------------- per-project mapping
@router.get("/projects/{key}/jira")
async def get_mapping(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "project.write", p)
    return svc.mapping_view(p)


@router.put("/projects/{key}/jira")
async def put_mapping(key: str, body: dict[str, Any], actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "project.write", p)
    out = svc.save_mapping(uow, actor.ws.id, actor.person.id, p, body)
    await uow.commit()
    return out


class LinkIn(BaseModel):
    pairs: list[dict[str, str]] = Field(default_factory=list)  # [{"item": "WEB-1", "issue_key": "WEB-10"}]
    by_summary: bool = False


@router.post("/projects/{key}/jira:link-existing")
async def link_existing(key: str, body: LinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Attach existing Jira issues to items (explicit pairs, or equal summaries). Nothing is created or changed in Jira."""
    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "project.write", p)
    out = await svc.link_existing(
        uow, actor.ws.id, actor.person.id, p, pairs=body.pairs, by_summary=body.by_summary
    )
    await uow.commit()
    return out


# ---------------------------------------------------------------- per item
@router.get("/items/{key}/jira", dependencies=[needs("item.read", item="key")])
async def item_jira(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await svc.item_status(uow, actor.ws.id, key)


@router.post("/items/{key}/jira:push", dependencies=[needs("item.write_overlay", item="key")])
async def push_now(key: str, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Manual push. An explicit push is an opt-in for this item."""
    it, changed = await svc.opt_in_for_push(uow, actor.ws.id, actor.person.id, key)
    if changed:
        await uow.commit()  # the push below runs in its own session and must see the opt-in
    return await svc.push_now(request.app.state.session_factory, it.id)


# ---------------------------------------------------------------- inbound webhook (no session: a shared secret authenticates it)
@hook.post("/webhook/{ws}")
async def webhook(ws: str, request: Request, token: str = "") -> dict[str, Any]:
    factory = request.app.state.session_factory
    ws_id, secret = await svc.webhook_workspace(factory, ws)
    svc.check_webhook_auth(secret, token, request.headers.get("x-hub-signature", ""), await request.body())
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ApiError(400, "invalid", "body is not JSON") from exc
    return await inboundmod.handle(factory, ws_id, payload)
