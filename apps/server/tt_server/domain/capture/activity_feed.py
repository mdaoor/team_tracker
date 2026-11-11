"""Read model of activities: mine, one person's, or the active work of the people I manage or lead."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.enums import ActivityStatus

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import redact_activity, visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import row_dict


async def _team_group(uow: UnitOfWork, ws_id: str, person_id: str, acl: AclContext) -> set[str]:
    """People the viewer manages plus members of teams they lead (themselves excluded)."""
    led = select(m.Membership.person_id).where(
        m.Membership.team_id.in_(select(m.Team.id).where(m.Team.workspace_id == ws_id, m.Team.lead_id == person_id)),
        m.Membership.left_at.is_(None),
    )  # fmt: skip
    group = set(acl.managed_ids) | set((await uow.session.scalars(led)).all())
    group.discard(person_id)
    return group


async def list_activities(
    uow: UnitOfWork,
    ws_id: str,
    person_id: str,
    acl: AclContext,
    *,
    status: ActivityStatus | None,
    person: str,
    limit: int,
    team: str | None,
) -> list[dict[str, Any]]:
    """My activities, one person's, or (`team=mine`) the active work of the people I manage or lead (Phase 14 "team now")."""
    pid = person_id
    group: set[str] | None = None
    if team == "mine":
        group = await _team_group(uow, ws_id, person_id, acl)
    elif person != "me":
        pid = (
            await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.handle == person
                )
            )
            or ""
        )
    whose = m.Activity.person_id == pid if group is None else m.Activity.person_id.in_(group)
    stmt = (
        select(m.Activity, m.WorkItem.key, m.WorkItem.title, m.Project.key)
        .join(m.WorkItem, m.WorkItem.id == m.Activity.work_item_id)
        .join(m.Project, m.Project.id == m.Activity.project_id)
        .where(whose, m.Project.workspace_id == ws_id)
    )
    stmt = visible(stmt, acl, m.Activity)
    if status:
        stmt = stmt.where(m.Activity.status == status)
    rows = (
        await uow.session.execute(stmt.order_by(m.Activity.started_at.desc()).limit(min(limit, 200)))
    ).all()
    handles = dict(
        (
            await uow.session.execute(
                select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == ws_id,
                    m.WorkspaceMember.person_id.in_({a.person_id for a, *_ in rows}),
                )
            )
        ).all()
    )
    return [
        {
            **redact_activity(acl, row_dict(a)),
            "item_key": k,
            "item_title": t,
            "project_key": pk,
            "person": handles.get(a.person_id),
        }
        for a, k, t, pk in rows
    ]
