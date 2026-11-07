"""Variables and statistics a presentation template is instantiated with, computed for a scope as the viewer sees it."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from tt_server.acl.context import AclContext
from tt_server.clock import person_today
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain import deckdata
from tt_server.domain.snapshot import assembler
from tt_server.query import service


async def template_variables(
    uow: UnitOfWork,
    ws: m.Workspace,
    person: m.Person,
    member: m.WorkspaceMember,
    acl: AclContext,
    scope: str,
    range_days: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    """(`${...}` variables, `requires` statistics) of a scope and range for template instantiation."""
    kind, ref = assembler.parse_scope(scope)
    qc = deckdata.qctx(uow.session, ws, person, member, acl)
    flt, label = await assembler._scope_filter(qc, kind, ref)  # noqa: SLF001

    stmt = await service.filtered_items(
        qc, {"and": [flt, {"not": {"type": {"in": ["epic", "phase", "deliverable"]}}}]}, m.WorkItem.id
    )
    items = int(await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    plan_stmt = await service.filtered_items(
        qc, {"and": [flt, {"type": {"in": ["phase", "deliverable"]}}]}, m.WorkItem.id
    )
    plan_items = int(await uow.session.scalar(select(func.count()).select_from(plan_stmt.subquery())) or 0)
    projects = (
        (
            await uow.session.scalar(
                select(func.count())
                .select_from(m.Project)
                .where(m.Project.workspace_id == ws.id, m.Project.deleted_at.is_(None))
            )
            or 0
        )
        if kind == "workspace"
        else 1
    )
    pname = ""
    if kind == "project":
        pname = (
            await uow.session.scalar(
                select(m.Project.name).where(m.Project.workspace_id == ws.id, m.Project.key == ref.upper())
            )
            or ""
        )
    today = person_today(person)
    variables = {
        "scope.label": label, "scope.kind": kind, "scope.ref": ref, "scope.items": items, "scope.projects": projects,
        "project.key": ref.upper() if kind == "project" else "", "project.name": pname, "range.days": range_days,
        "range.from": (today.fromordinal(today.toordinal() - range_days)).isoformat(), "range.to": today.isoformat(),
        "viewer.handle": member.handle, "workspace.name": ws.name, "today": today.isoformat(),
    }  # fmt: skip
    return variables, {"scope.items": items, "scope.projects": projects, "scope.plan_items": plan_items}
