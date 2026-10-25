"""Jira administration: connection settings, per-project mapping, linking existing issues, manual push, webhook auth.

The router parses and authorises; these functions validate, write and emit. They never commit (the router owns the
unit of work), except where a push must see the committed opt-in, which the router sequences itself.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.enums import EventSource, TargetType
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, row_dict
from tt_server.domain.errors import Conflict, Invalid, NotFound, Unauthenticated, Unavailable
from tt_server.domain.lookup import visible_item
from tt_server.integrations.jira import crypto, mapping
from tt_server.integrations.jira import sync as syncmod
from tt_server.integrations.jira.client import JiraError, JiraUnavailable


def connection_view(c: m.JiraConnection | None, ws_slug: str, secret: str | None = None) -> dict[str, Any]:
    """The connection as the admin UI sees it; the webhook secret is included only when freshly created or rotated."""
    if c is None:
        return {"configured": False}
    out: dict[str, Any] = {
        "configured": True, "base_url": c.base_url, "email": c.email, "enabled": c.enabled, "bot_account_id": c.bot_account_id,
        "webhook_path": f"/api/v1/integrations/jira/webhook/{ws_slug}",
    }  # fmt: skip
    if secret:
        out["webhook_secret"] = secret  # shown once, on create / rotate
    return out


async def get_connection(uow: UnitOfWork, ws_id: str, *, need: bool = True) -> m.JiraConnection | None:
    """The workspace's Jira connection; with `need`, a missing one is a 409 telling the admin to connect first."""
    c = await uow.session.scalar(
        select(m.JiraConnection).where(
            m.JiraConnection.workspace_id == ws_id, m.JiraConnection.deleted_at.is_(None)
        )
    )
    if c is None and need:
        raise Conflict("not_configured", "connect Jira first (PUT /integrations/jira)")
    return c


def _configured(uow: UnitOfWork, ws_id: str, actor_id: str, after: dict[str, Any]) -> None:
    emit(
        uow, workspace_id=ws_id, actor_id=actor_id, entity_type=TargetType.PERSON, entity_id=actor_id,
        event_type="integration.jira_configured", payload={"after": after},
    )  # fmt: skip


async def save_connection(
    uow: UnitOfWork,
    ws_id: str,
    actor_id: str,
    *,
    base_url: str,
    email: str,
    api_token: str | None,
    enabled: bool,
) -> tuple[m.JiraConnection, str | None]:
    """Create or update the connection. Returns it with the webhook secret when one was just generated."""
    c = await get_connection(uow, ws_id, need=False)
    secret = None
    if c is None:
        if not api_token:
            raise Invalid("invalid", "api_token is required the first time", pointer="/api_token")
        secret = secrets.token_urlsafe(24)
        c = m.JiraConnection(
            id=new_id(), workspace_id=ws_id, base_url=base_url.rstrip("/"), email=email,
            token_enc=crypto.encrypt(api_token), webhook_secret_enc=crypto.encrypt(secret), enabled=enabled,
        )  # fmt: skip
        uow.session.add(c)
    else:
        c.base_url, c.email, c.enabled = base_url.rstrip("/"), email, enabled
        if api_token:
            c.token_enc = crypto.encrypt(api_token)
            c.bot_account_id = None  # re-learned by the next connection test
    _configured(uow, ws_id, actor_id, {"base_url": c.base_url, "enabled": c.enabled})
    return c, secret


async def rotate_secret(uow: UnitOfWork, ws_id: str, actor_id: str) -> tuple[m.JiraConnection, str]:
    """Replace the inbound webhook secret; the new one is shown once."""
    c = await get_connection(uow, ws_id)
    assert c is not None  # noqa: S101
    secret = secrets.token_urlsafe(24)
    c.webhook_secret_enc = crypto.encrypt(secret)
    _configured(uow, ws_id, actor_id, {"rotated_secret": True})
    return c, secret


async def check_connection(uow: UnitOfWork, ws_id: str, actor_id: str, project: str | None) -> dict[str, Any]:
    """Authenticate, learn the integration user (its edits are never echoed back), and optionally fetch a project's options.

    The result carries `ok`; only an ok result has changed anything (the router commits then).
    """
    c = await get_connection(uow, ws_id)
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
    _configured(uow, ws_id, actor_id, {"tested": True, "account": c.bot_account_id})
    return {
        "ok": True,
        "account": {"id": me.get("accountId"), "name": me.get("displayName")},
        "options": opts,
    }


async def sync_log(uow: UnitOfWork, ws_id: str, *, limit: int, item: str | None) -> list[dict[str, Any]]:
    """Recent sync log rows of the workspace, newest first, optionally for one Jira issue key."""
    q = select(m.JiraSyncLog).where(m.JiraSyncLog.workspace_id == ws_id)
    if item:
        q = q.where(m.JiraSyncLog.issue_key == item)
    rows = (
        await uow.session.scalars(q.order_by(m.JiraSyncLog.created_at.desc()).limit(min(limit, 200)))
    ).all()
    return [row_dict(r) for r in rows]


def mapping_view(p: m.Project) -> dict[str, Any]:
    """A project's Jira mapping as stored."""
    return {
        "project": p.key,
        "config": syncmod.project_config(p),
        "jira_project": (p.external_refs or {}).get("jira"),
    }


def save_mapping(
    uow: UnitOfWork, ws_id: str, actor_id: str, p: m.Project, body: dict[str, Any]
) -> dict[str, Any]:
    """Validate and store a project's Jira mapping, remembering the Jira project key as an external ref."""
    try:
        cfg = mapping.validate_config(body)
    except ValueError as exc:
        raise Invalid("invalid_mapping", str(exc), pointer="/") from exc
    p.settings = {**(p.settings or {}), "jira": cfg}
    if cfg.get("project_key"):
        p.external_refs = {**(p.external_refs or {}), "jira": cfg["project_key"]}
    emit(
        uow, workspace_id=ws_id, actor_id=actor_id, entity_type=TargetType.PROJECT, entity_id=p.id,
        event_type="project.jira_configured", payload={"after": {"enabled": bool(cfg.get("enabled")), "project_key": cfg.get("project_key")}},
    )  # fmt: skip
    return {"project": p.key, "config": cfg}


async def _summary_pairs(
    uow: UnitOfWork, client: Any, p: m.Project, project_key: str, pairs: list[dict[str, str]]
) -> None:
    """Extend `pairs` with items whose title equals the summary of an unlinked Jira issue."""
    s = uow.session
    issues = await client.search(f'project = "{project_key}"')
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


async def link_existing(
    uow: UnitOfWork,
    ws_id: str,
    actor_id: str,
    p: m.Project,
    *,
    pairs: list[dict[str, str]],
    by_summary: bool,
) -> dict[str, Any]:
    """Attach existing Jira issues to items (explicit pairs, or equal summaries). Nothing is created or changed in Jira."""
    cfg = syncmod.project_config(p)
    c = await get_connection(uow, ws_id)
    assert c is not None  # noqa: S101
    if not cfg.get("project_key"):
        raise Conflict("not_configured", "set the project's Jira mapping first")
    s = uow.session
    client = syncmod.make_client(c)
    linked: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    try:
        todo = list(pairs)
        if by_summary:
            await _summary_pairs(uow, client, p, cfg["project_key"], todo)
        for pair in todo:
            it = await visible_item(uow, ws_id, pair["item"])
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
                uow, workspace_id=ws_id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=it.id,
                event_type="item.jira_linked", payload={"after": {"issue_key": issue["key"], "via": "link-existing"}}, source=EventSource.IMPORT,
            )  # fmt: skip
            linked.append(pair)
    except JiraUnavailable as exc:
        raise Unavailable("jira_unavailable", str(exc)) from exc
    finally:
        await client.aclose()
    return {"linked": linked, "skipped": skipped}


async def item_status(uow: UnitOfWork, ws_id: str, key: str) -> dict[str, Any]:
    """Whether an item is linked to a Jira issue, with the link's sync state (or whether it is opted in)."""
    it = await visible_item(uow, ws_id, key)
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


async def opt_in_for_push(uow: UnitOfWork, ws_id: str, actor_id: str, key: str) -> tuple[m.WorkItem, bool]:
    """An explicit push is an opt-in for the item. Returns the item and whether the opt-in was newly recorded."""
    it = await visible_item(uow, ws_id, key)
    if (it.external_refs or {}).get("jira_opt_in") == "true":
        return it, False
    it.external_refs = {**(it.external_refs or {}), "jira_opt_in": "true"}
    emit(
        uow, workspace_id=ws_id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=it.id,
        event_type="item.updated", payload={"after": {"jira_opt_in": True}},
    )  # fmt: skip
    return it, True


async def push_now(factory: async_sessionmaker[AsyncSession], item_id: str) -> dict[str, Any]:
    """Push one item to Jira now (forced) and report the outcome."""
    try:
        res = await syncmod.push_item(factory, item_id, force=True)
    except JiraUnavailable as exc:
        raise Unavailable("jira_unavailable", str(exc)) from exc
    return {"status": res.status, "key": res.key, "reason": res.reason, "changed": res.changed}


async def webhook_workspace(factory: async_sessionmaker[AsyncSession], slug: str) -> tuple[str, str]:
    """(workspace id, webhook secret) for the inbound webhook of a workspace slug; NotFound when not connected."""
    async with factory() as s:
        w = await s.scalar(
            select(m.Workspace).where(m.Workspace.slug == slug, m.Workspace.deleted_at.is_(None))
        )
        c = await syncmod.connection(s, w.id) if w else None
        if w is None or c is None:
            raise NotFound("not_found", "unknown workspace")
        return w.id, crypto.decrypt(c.webhook_secret_enc)


def check_webhook_auth(secret: str, token: str, signature: str, body: bytes) -> None:
    """Accept the shared secret as `?token=` or a valid HMAC-SHA256 `X-Hub-Signature`; otherwise 401."""
    good = hmac.compare_digest(token, secret) or (
        signature.startswith("sha256=")
        and hmac.compare_digest(signature[7:], hmac.new(secret.encode(), body, "sha256").hexdigest())
    )
    if not good:
        raise Unauthenticated("unauthorized", "bad webhook secret")
