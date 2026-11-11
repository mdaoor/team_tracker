"""ContextPack additions for management roles (Phase 14): what the LLM and the forms need to resolve names offline.

Reporters get empty lists, so the pack stays small for them and the shape stays stable for everyone.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import ApprovalStatus, Decision, ProjectKind

from tt_server.acl.context import load_context
from tt_server.acl.modes import modes_for
from tt_server.db import models as m
from tt_server.domain.overlay import review


async def role_context(session: AsyncSession, ws: m.Workspace, person_id: str) -> dict[str, Any]:
    """Describe the person's role in the workspace: visible modes, managed teams and projects."""
    acl = await load_context(session, ws.id, person_id)
    modes = await modes_for(session, acl)
    managing = len(modes) > 1
    teams: list[dict[str, Any]] = []
    projects: list[str] = []
    if managing:
        led = select(m.Team.id).where(m.Team.workspace_id == ws.id, m.Team.lead_id == person_id)
        mine = select(m.Membership.team_id).where(
            m.Membership.person_id == person_id, m.Membership.left_at.is_(None)
        )
        wide = acl.is_admin or acl.can("team.write")
        stmt = select(m.Team).where(m.Team.workspace_id == ws.id, m.Team.deleted_at.is_(None))
        if not wide:
            stmt = stmt.where(or_(m.Team.id.in_(led), m.Team.id.in_(mine)))
        for t in (await session.scalars(stmt.order_by(m.Team.slug).limit(100))).all():
            handles = (
                await session.scalars(
                    select(m.WorkspaceMember.handle)
                    .join(m.Membership, m.Membership.person_id == m.WorkspaceMember.person_id)
                    .where(
                        m.Membership.team_id == t.id,
                        m.Membership.left_at.is_(None),
                        m.WorkspaceMember.workspace_id == ws.id,
                    )
                    .order_by(m.WorkspaceMember.handle)
                )
            ).all()
            teams.append({"slug": t.slug, "name": t.name, "kind": t.kind.value, "members": list(handles)})
        pstmt = select(m.Project.key).where(
            m.Project.workspace_id == ws.id,
            m.Project.deleted_at.is_(None),
            m.Project.kind != ProjectKind.INBOX,
        )
        if not acl.is_admin:
            pstmt = pstmt.where(m.Project.id.in_(acl.manager_project_ids))
        projects = list((await session.scalars(pstmt.order_by(m.Project.key).limit(200))).all())
    queue = 0
    if acl.can("review_queue.resolve") or acl.managed_ids:
        queue = len(await review.list_queue(session, ws.id, person_id))
    rows = (
        await session.execute(
            select(m.Approval, m.Project.key, m.WorkspaceMember.handle)
            .join(m.ApprovalDecision, m.ApprovalDecision.approval_id == m.Approval.id)
            .join(m.Project, m.Project.id == m.Approval.project_id)
            .outerjoin(
                m.WorkspaceMember,
                (m.WorkspaceMember.person_id == m.Approval.requested_by)
                & (m.WorkspaceMember.workspace_id == ws.id),
            )
            .where(
                m.ApprovalDecision.approver_id == person_id,
                m.ApprovalDecision.decision == Decision.PENDING,
                m.Approval.status == ApprovalStatus.PENDING,
                m.Project.workspace_id == ws.id,
            )
            .order_by(m.Approval.created_at, m.Approval.id)
            .limit(100)
        )
    ).all()
    pending = [
        {
            "id": a.id,
            "title": a.title,
            "project_key": key,
            "requested_by": handle,
            "requested_at": a.created_at.isoformat(),
        }
        for a, key, handle in rows
    ]
    return {
        "my_role_modes": modes,
        "my_teams_with_members": teams,
        "my_projects_as_manager": projects,
        "review_queue_count": queue,
        "pending_approvals": pending,
        "pending_approvals_count": len(pending),
    }
