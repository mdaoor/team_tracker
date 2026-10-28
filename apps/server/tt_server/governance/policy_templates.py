"""Policy-template lifecycle behind `/policy-templates` (Phase 8): list, create, copy, publish; plus the shared audit helper."""

from __future__ import annotations

from typing import Any

import tt_cel
from sqlalchemy import select
from tt_schema.base import new_id

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit_admin
from tt_server.domain.errors import Invalid, NotFound
from tt_server.governance import service


def _audit(uow: UnitOfWork, ws: m.Workspace, actor_id: str, event: str, **payload: Any) -> None:
    emit_admin(uow, workspace_id=ws.id, actor_id=actor_id, event_type=event, payload={"after": payload})


# ---------------------------------------------------------------- templates


async def list_templates(uow: UnitOfWork, ws_id: str) -> list[m.PolicyTemplate]:
    """Builtin and workspace templates, a workspace template shadowing a builtin of the same key."""
    rows = (
        await uow.session.scalars(
            select(m.PolicyTemplate)
            .where(
                (m.PolicyTemplate.workspace_id == ws_id) | m.PolicyTemplate.workspace_id.is_(None),
                m.PolicyTemplate.deleted_at.is_(None),
            )
            .order_by(m.PolicyTemplate.key)
        )
    ).all()
    shadowed = {r.key for r in rows if r.workspace_id}
    return [r for r in rows if r.workspace_id or r.key not in shadowed]


async def create_template(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any]
) -> m.PolicyTemplate:
    """Create a workspace template after compiling its condition and detail with the default parameters."""
    try:
        params = tuple(tt_cel.Param(**p) for p in data["params"])
        tt_cel.compile(tt_cel.render(data["cel_source"], params, {p.name: p.default for p in params}))
        tt_cel.compile_detail(data["detail"], params, {p.name: p.default for p in params})
    except (tt_cel.CelError, TypeError) as exc:
        raise Invalid("invalid_condition", getattr(exc, "message", str(exc)), pointer="/cel_source") from exc
    if data["published_scope"] not in {"workspace", "shared"}:
        raise Invalid("invalid_scope", "published_scope is workspace or shared", pointer="/published_scope")
    row = m.PolicyTemplate(
        id=new_id(),
        key=data["key"],
        name=data["name"],
        category=data["category"],
        cel_source=data["cel_source"],
        description=data["description"],
        workspace_id=ws.id,
        is_builtin=False,
        published_scope=data["published_scope"],
        params={
            "parameters": data["params"],
            "target": data["target"],
            "trigger": data["trigger"],
            "severity": data["severity"].value,
            "schedule": data["schedule"],
            "event_types": data["event_types"],
            "detail": data["detail"],
            "candidate_filter": data["candidate_filter"],
        },
    )
    uow.session.add(row)
    await uow.session.flush()
    _audit(uow, ws, actor_id, "policy.template_created", template=row.key)
    return row


async def copy_template(uow: UnitOfWork, ws: m.Workspace, actor_id: str, tid: str) -> m.PolicyTemplate:
    """Import a template another workspace published as `shared`: an independent copy (§14.2)."""
    row = await uow.session.scalar(
        select(m.PolicyTemplate).where(
            m.PolicyTemplate.id == tid,
            (m.PolicyTemplate.published_scope == "shared")
            | (m.PolicyTemplate.workspace_id == ws.id)
            | m.PolicyTemplate.workspace_id.is_(None),
        )
    )
    if row is None:
        raise NotFound("not_found", "template not found or not shared")
    copy = await service.copy_template(uow.session, row, ws.id, actor=actor_id)
    _audit(uow, ws, actor_id, "policy.template_created", template=copy.key, copied_from=row.id)
    return copy


async def publish_template(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, tid: str, *, shared: bool
) -> m.PolicyTemplate:
    """Mark a workspace template as shared with other workspaces (or back to workspace-only)."""
    row = await uow.session.scalar(
        select(m.PolicyTemplate).where(m.PolicyTemplate.id == tid, m.PolicyTemplate.workspace_id == ws.id)
    )
    if row is None:
        raise NotFound("not_found", "template not found")
    row.published_scope = "shared" if shared else "workspace"
    _audit(uow, ws, actor_id, "policy.template_published", template=row.key, scope=row.published_scope)
    return row
