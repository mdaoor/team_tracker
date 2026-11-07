"""Access grants: manual ones, and system grants propagated from project / product membership (§4.4)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, GrantLevel, SubjectType, TargetType

from tt_server.acl.roles import get_role
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid, NotFound

SYSTEM = "system"  # `granted_by` marker: system grants are the only ones membership changes may delete


async def _resolve_subject(uow: UnitOfWork, ws_id: str, subject_type: SubjectType, ref: str) -> str:
    s = uow.session
    if subject_type == SubjectType.PERSON:
        pid = await s.scalar(
            select(m.WorkspaceMember.person_id).where(
                m.WorkspaceMember.workspace_id == ws_id,
                m.WorkspaceMember.handle == (ref if ref.startswith("@") else "@" + ref),
            )
        )
        if pid:
            return str(pid)
    elif subject_type == SubjectType.TEAM:
        tid = await s.scalar(
            select(m.Team.id).where(m.Team.workspace_id == ws_id, (m.Team.id == ref) | (m.Team.slug == ref))
        )
        if tid:
            return str(tid)
    else:
        return (await get_role(s, ref, ws_id)).id
    raise NotFound("not_found", f"unknown {subject_type.value} {ref!r}", pointer="/subject")


async def _resolve_object(uow: UnitOfWork, ws_id: str, object_type: TargetType, ref: str) -> str:
    s = uow.session
    model: Any = {
        TargetType.PROJECT: m.Project,
        TargetType.PRODUCT: m.Product,
        TargetType.WORK_ITEM: m.WorkItem,
        TargetType.TEAM: m.Team,
    }.get(object_type)
    if model is None:
        raise Invalid("not_grantable", f"cannot grant on a {object_type.value}", pointer="/object_type")
    if object_type == TargetType.TEAM:
        by_name = model.slug == ref
    else:
        by_name = model.key == ref.upper()
    stmt = select(model.id).where((model.id == ref) | by_name)
    if object_type == TargetType.WORK_ITEM:
        stmt = stmt.join(m.Project, m.Project.id == m.WorkItem.project_id).where(
            m.Project.workspace_id == ws_id
        )
    elif hasattr(model, "workspace_id"):
        stmt = stmt.where(model.workspace_id == ws_id)
    oid = await s.scalar(stmt)
    if oid is None:
        raise NotFound("not_found", f"unknown {object_type.value} {ref!r}", pointer="/object_id")
    return str(oid)


async def create_manual(uow: UnitOfWork, ws: m.Workspace, body: Any, *, granted_by: str) -> m.AccessGrant:
    sid = await _resolve_subject(uow, ws.id, body.subject_type, body.subject)
    oid = await _resolve_object(uow, ws.id, body.object_type, body.object_id)
    row = m.AccessGrant(
        id=new_id(),
        workspace_id=ws.id,
        subject_type=body.subject_type,
        subject_id=sid,
        object_type=body.object_type,
        object_id=oid,
        level=body.level,
        inherit_to_children=body.inherit_to_children,
        granted_by=granted_by,
        expires_at=body.expires_at,
    )
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=granted_by,
        entity_type=body.object_type,
        entity_id=oid,
        event_type="grant.created",
        payload={
            "after": {"subject": f"{body.subject_type.value}:{body.subject}", "level": body.level.value}
        },
        source=EventSource.OVERLAY,
    )
    return row


async def on_project_member_added(
    uow: UnitOfWork, ws: m.Workspace, project: m.Project, person_id: str
) -> None:
    """`project_member.added` -> a system read grant on the project (private items still exclude them)."""
    s = uow.session
    exists = await s.scalar(
        select(m.AccessGrant.id).where(
            m.AccessGrant.subject_type == SubjectType.PERSON,
            m.AccessGrant.subject_id == person_id,
            m.AccessGrant.object_type == TargetType.PROJECT,
            m.AccessGrant.object_id == project.id,
            m.AccessGrant.granted_by.is_(None),
            m.AccessGrant.deleted_at.is_(None),
        )
    )
    if exists is None:
        s.add(
            m.AccessGrant(
                id=new_id(),
                workspace_id=ws.id,
                subject_type=SubjectType.PERSON,
                subject_id=person_id,
                object_type=TargetType.PROJECT,
                object_id=project.id,
                level=GrantLevel.READ,
                inherit_to_children=True,
                granted_by=None,
            )
        )  # granted_by NULL = system
        await s.flush()


async def on_project_member_removed(
    uow: UnitOfWork, ws: m.Workspace, project: m.Project, person_id: str
) -> None:
    """Removal deletes system grants only; a manual grant for the same person survives."""
    for g in (
        await uow.session.scalars(
            select(m.AccessGrant).where(
                m.AccessGrant.subject_type == SubjectType.PERSON,
                m.AccessGrant.subject_id == person_id,
                m.AccessGrant.object_type == TargetType.PROJECT,
                m.AccessGrant.object_id == project.id,
                m.AccessGrant.granted_by.is_(None),
                m.AccessGrant.deleted_at.is_(None),
            )
        )
    ).all():
        g.deleted_at = utcnow()


async def on_team_linked_to_product(
    uow: UnitOfWork, ws: m.Workspace, product: m.Product, team_id: str
) -> None:
    s = uow.session
    exists = await s.scalar(
        select(m.AccessGrant.id).where(
            m.AccessGrant.subject_type == SubjectType.TEAM,
            m.AccessGrant.subject_id == team_id,
            m.AccessGrant.object_type == TargetType.PRODUCT,
            m.AccessGrant.object_id == product.id,
            m.AccessGrant.granted_by.is_(None),
            m.AccessGrant.deleted_at.is_(None),
        )
    )
    if exists is None:
        s.add(
            m.AccessGrant(
                id=new_id(),
                workspace_id=ws.id,
                subject_type=SubjectType.TEAM,
                subject_id=team_id,
                object_type=TargetType.PRODUCT,
                object_id=product.id,
                level=GrantLevel.READ,
                inherit_to_children=True,
                granted_by=None,
            )
        )
        await s.flush()
