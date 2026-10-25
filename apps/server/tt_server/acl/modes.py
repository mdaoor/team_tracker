"""Role modes the client may offer (Phase 14): a lens over the person's real roles, never a permission."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import ProjectKind

from tt_server.acl.context import AclContext
from tt_server.db import models as m

MODES = ("reporter", "manager", "scrum_master", "coach", "pm", "admin")


def role_modes(acl: AclContext, *, manages_real_project: bool | None = None) -> list[str]:
    keys = {a.role_key for a in acl.assignments}
    out = ["reporter"]
    if acl.is_admin or "manager" in keys or acl.managed_ids:
        out.append("manager")
    if "scrum_master" in keys:
        out.append("scrum_master")
    if "coach" in keys:
        out.append("coach")
    owns = bool(acl.manager_project_ids) if manages_real_project is None else manages_real_project
    if acl.is_admin or keys & {"project_owner", "project_manager"} or owns:
        out.append("pm")
    if acl.is_admin:
        out.append("admin")
    return [m for m in MODES if m in out]


def role_keys(acl: AclContext) -> list[str]:
    return sorted({a.role_key for a in acl.assignments} | ({"workspace_admin"} if acl.is_admin else set()))


async def modes_for(session: AsyncSession, acl: AclContext) -> list[str]:
    """`role_modes`, where owning only your personal Inbox does not make you a project manager."""
    real = False
    if acl.manager_project_ids:
        real = bool(
            await session.scalar(
                select(m.Project.id)
                .where(m.Project.id.in_(acl.manager_project_ids), m.Project.kind != ProjectKind.INBOX)
                .limit(1)
            )
        )
    return role_modes(acl, manages_real_project=real)
