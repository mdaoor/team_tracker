"""Phase 10 API: Jira connection, per-project mapping, manual push/link, sync log and the signed inbound webhook (ADR-0015)."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import EventSource, TargetType

from tt_server.api.authz import check_project, needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _item, _project
from tt_server.db import models as m
from tt_server.domain.common import emit, row_dict
from tt_server.integrations.jira import crypto, mapping
from tt_server.integrations.jira import inbound as inboundmod
from tt_server.integrations.jira import sync as syncmod
from tt_server.integrations.jira.client import JiraError, JiraUnavailable

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["jira"])
hook = APIRouter(prefix="/api/v1/integrations/jira", tags=["jira"])


def _view(c: m.JiraConnection | None, ws_slug: str, secret: str | None = None) -> dict[str, Any]:
    if c is None:
        return {"configured": False}
    out: dict[str, Any] = {
        "configured": True, "base_url": c.base_url, "email": c.email, "enabled": c.enabled, "bot_account_id": c.bot_account_id,
        "webhook_path": f"/api/v1/integrations/jira/webhook/{ws_slug}",
    }  # fmt: skip
    if secret:
        out["webhook_secret"] = secret  # shown once, on create / rotate
    return out


async def _conn(uow: Uow, ws_id: str, *, need: bool = True) -> m.JiraConnection | None:
    c = await uow.session.scalar(
        select(m.JiraConnection).where(
            m.JiraConnection.workspace_id == ws_id, m.JiraConnection.deleted_at.is_(None)
        )
    )
    if c is None and need:
        raise ApiError(409, "not_configured", "connect Jira first (PUT /integrations/jira)")
    return c


class ConnIn(BaseModel):
    base_url: str = Field(pattern=r"^https://[^\s/]+(/.*)?$")
    email: str
    api_token: str | None = Field(default=None, min_length=4)
    enabled: bool = True


@router.get("/integrations/jira", dependencies=[needs("role.admin")])
async def get_connection(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return _view(await _conn(uow, actor.ws.id, need=False), actor.ws.slug)


@router.put("/integrations/jira", dependencies=[needs("role.admin")])
async def put_connection(body: ConnIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    c = await _conn(uow, actor.ws.id, need=False)
    secret = None
    if c is None:
        if not body.api_token:
            raise ApiError(400, "invalid", "api_token is required the first time", pointer="/api_token")
        secret = secrets.token_urlsafe(24)
        c = m.JiraConnection(
            id=new_id(), workspace_id=actor.ws.id, base_url=body.base_url.rstrip("/"), email=body.email,
            token_enc=crypto.encrypt(body.api_token), webhook_secret_enc=crypto.encrypt(secret), enabled=body.enabled,
        )  # fmt: skip
        uow.session.add(c)
    else:
        c.base_url, c.email, c.enabled = body.base_url.rstrip("/"), body.email, body.enabled
        if body.api_token:
            c.token_enc = crypto.encrypt(body.api_token)
            c.bot_account_id = None  # re-learned by the next connection test
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PERSON, entity_id=actor.person.id,
        event_type="integration.jira_configured", payload={"after": {"base_url": c.base_url, "enabled": c.enabled}},
    )  # fmt: skip
    await uow.commit()
    return _view(c, actor.ws.slug, secret)


@router.post("/integrations/jira:rotate-secret", dependencies=[needs("role.admin")])
async def rotate_secret(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    c = await _conn(uow, actor.ws.id)
    assert c is not None  # noqa: S101
    secret = secrets.token_urlsafe(24)
    c.webhook_secret_enc = crypto.encrypt(secret)
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PERSON, entity_id=actor.person.id,
        event_type="integration.jira_configured", payload={"after": {"rotated_secret": True}},
    )  # fmt: skip
    await uow.commit()
    return _view(c, actor.ws.slug, secret)


@router.post("/integrations/jira:test", dependencies=[needs("role.admin")])
async def test_connection(actor: CurrentActor, uow: Uow, project: str | None = None) -> dict[str, Any]:
    """Authenticate, learn the integration user (its edits are never echoed back), and optionally fetch a project's options."""
    c = await _conn(uow, actor.ws.id)
    assert c is not None  # noqa: S101
    client = syncmod.make_client(c)
    try:
        me = await client.myself()
        c.bot_account_id = me.get("accountId")
        opts = await client.project_options(project) if project else None
    except JiraError as exc:
        return {"ok": False, "error": exc.message, "status": exc.status}
    except JiraUnavailable as exc:
        return {"ok": False, "error": str(exc), "status": 503}
    finally:
        await client.aclose()
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PERSON, entity_id=actor.person.id,
        event_type="integration.jira_configured", payload={"after": {"tested": True, "account": c.bot_account_id}},
    )  # fmt: skip
    await uow.commit()
    return {
        "ok": True,
        "account": {"id": me.get("accountId"), "name": me.get("displayName")},
        "options": opts,
    }


@router.get("/integrations/jira/log", dependencies=[needs("role.admin")])
async def sync_log(
    actor: CurrentActor, uow: Uow, limit: int = Query(50, ge=1, le=1000), item: str | None = None
) -> dict[str, Any]:
    q = select(m.JiraSyncLog).where(m.JiraSyncLog.workspace_id == actor.ws.id)
    if item:
        q = q.where(m.JiraSyncLog.issue_key == item)
    rows = (
        await uow.session.scalars(q.order_by(m.JiraSyncLog.created_at.desc()).limit(min(limit, 200)))
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


# ---------------------------------------------------------------- per-project mapping
@router.get("/projects/{key}/jira")
async def get_mapping(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "project.write", p)
    return {
        "project": p.key,
        "config": syncmod.project_config(p),
        "jira_project": (p.external_refs or {}).get("jira"),
    }


@router.put("/projects/{key}/jira")
async def put_mapping(key: str, body: dict[str, Any], actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "project.write", p)
    try:
        cfg = mapping.validate_config(body)
    except ValueError as exc:
        raise ApiError(400, "invalid_mapping", str(exc), pointer="/") from exc
    p.settings = {**(p.settings or {}), "jira": cfg}
    if cfg.get("project_key"):
        p.external_refs = {**(p.external_refs or {}), "jira": cfg["project_key"]}
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PROJECT, entity_id=p.id,
        event_type="project.jira_configured", payload={"after": {"enabled": bool(cfg.get("enabled")), "project_key": cfg.get("project_key")}},
    )  # fmt: skip
    await uow.commit()
    return {"project": p.key, "config": cfg}


class LinkIn(BaseModel):
    pairs: list[dict[str, str]] = Field(default_factory=list)  # [{"item": "WEB-1", "issue_key": "WEB-10"}]
    by_summary: bool = False


@router.post("/projects/{key}/jira:link-existing")
async def link_existing(key: str, body: LinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Attach existing Jira issues to items (explicit pairs, or equal summaries). Nothing is created or changed in Jira."""
    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "project.write", p)
    cfg = syncmod.project_config(p)
    c = await _conn(uow, actor.ws.id)
    assert c is not None  # noqa: S101
    if not cfg.get("project_key"):
        raise ApiError(409, "not_configured", "set the project's Jira mapping first")
    s = uow.session
    client = syncmod.make_client(c)
    linked: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    try:
        pairs = list(body.pairs)
        if body.by_summary:
            issues = await client.search(f'project = "{cfg["project_key"]}"')
            by = {str(i["fields"].get("summary", "")).strip().lower(): i["key"] for i in issues}
            already = set((await s.scalars(select(m.JiraLink.issue_key))).all())
            for it in (
                await s.scalars(
                    select(m.WorkItem).where(m.WorkItem.project_id == p.id, m.WorkItem.deleted_at.is_(None))
                )
            ).all():
                k = by.get(it.title.strip().lower())
                if k and k not in already and not (it.external_refs or {}).get("jira"):
                    pairs.append({"item": it.key, "issue_key": k})
                    already.add(k)
        for pair in pairs:
            it = await _item(uow, actor.ws.id, pair["item"])
            if it.project_id != p.id:
                skipped.append({**pair, "reason": "other_project"})
                continue
            if await s.scalar(
                select(m.JiraLink.id).where(
                    (m.JiraLink.item_id == it.id) | (m.JiraLink.issue_key == pair["issue_key"])
                )
            ):
                skipped.append({**pair, "reason": "already_linked"})
                continue
            try:
                issue = await client.get_issue(pair["issue_key"])
            except JiraError:
                skipped.append({**pair, "reason": "not_found_in_jira"})
                continue
            s.add(
                m.JiraLink(
                    id=new_id(),
                    item_id=it.id,
                    issue_key=issue["key"],
                    issue_id=str(issue.get("id", "")),
                    status="synced",
                )
            )
            it.external_refs = {**(it.external_refs or {}), "jira": issue["key"]}
            emit(
                uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=it.id,
                event_type="item.jira_linked", payload={"after": {"issue_key": issue["key"], "via": "link-existing"}}, source=EventSource.IMPORT,
            )  # fmt: skip
            linked.append(pair)
    except JiraUnavailable as exc:
        raise ApiError(503, "jira_unavailable", str(exc)) from exc
    finally:
        await client.aclose()
    await uow.commit()
    return {"linked": linked, "skipped": skipped}


# ---------------------------------------------------------------- per item
@router.get("/items/{key}/jira", dependencies=[needs("item.read", item="key")])
async def item_jira(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    it = await _item(uow, actor.ws.id, key)
    link = await uow.session.scalar(select(m.JiraLink).where(m.JiraLink.item_id == it.id))
    p = await uow.session.get(m.Project, it.project_id)
    assert p is not None  # noqa: S101
    cfg = syncmod.project_config(p)
    if link is None:
        return {
            "status": "not_linked", "linked": False, "project_enabled": bool(cfg.get("enabled")),
            "opted_in": syncmod.opted_in(it, cfg),
        }  # fmt: skip
    return {
        "linked": True, "status": link.status, "key": link.issue_key, "last_pushed_at": link.last_pushed_at,
        "last_inbound_at": link.last_inbound_at, "attempts": link.attempts, "error": link.last_error,
    }  # fmt: skip


@router.post("/items/{key}/jira:push", dependencies=[needs("item.write_overlay", item="key")])
async def push_now(key: str, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Manual push. An explicit push is an opt-in for this item."""
    it = await _item(uow, actor.ws.id, key)
    if (it.external_refs or {}).get("jira_opt_in") != "true":
        it.external_refs = {**(it.external_refs or {}), "jira_opt_in": "true"}
        emit(
            uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=it.id,
            event_type="item.updated", payload={"after": {"jira_opt_in": True}},
        )  # fmt: skip
        await uow.commit()
    try:
        res = await syncmod.push_item(request.app.state.session_factory, it.id, force=True)
    except JiraUnavailable as exc:
        raise ApiError(503, "jira_unavailable", str(exc)) from exc
    return {"status": res.status, "key": res.key, "reason": res.reason, "changed": res.changed}


# ---------------------------------------------------------------- inbound webhook (no session: a shared secret authenticates it)
@hook.post("/webhook/{ws}")
async def webhook(ws: str, request: Request, token: str = "") -> dict[str, Any]:
    factory = request.app.state.session_factory
    async with factory() as s:
        w = await s.scalar(
            select(m.Workspace).where(m.Workspace.slug == ws, m.Workspace.deleted_at.is_(None))
        )
        c = await syncmod.connection(s, w.id) if w else None
        if w is None or c is None:
            raise ApiError(404, "not_found", "unknown workspace")
        secret = crypto.decrypt(c.webhook_secret_enc)
        ws_id = w.id
    body = await request.body()
    sig = request.headers.get("x-hub-signature", "")
    good = hmac.compare_digest(token, secret) or (
        sig.startswith("sha256=")
        and hmac.compare_digest(sig[7:], hmac.new(secret.encode(), body, "sha256").hexdigest())
    )
    if not good:
        raise ApiError(401, "unauthorized", "bad webhook secret")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ApiError(400, "invalid", "body is not JSON") from exc
    return await inboundmod.handle(factory, ws_id, payload)
