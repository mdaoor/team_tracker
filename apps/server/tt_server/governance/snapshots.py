"""Reading metric snapshot series for a scope behind `GET /metric-snapshots` (Phase 8)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from tt_schema.enums import PolicyCategory, ScopeType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.errors import Forbidden, Invalid, NotFound


async def _resolve_scope(
    uow: UnitOfWork, ws: m.Workspace, acl: AclContext, person_id: str, scope_type: ScopeType, scope_id: str
) -> str:
    """Map the public scope reference (key, handle, slug...) to the id snapshots are stored under, checking access."""
    s = uow.session
    aggregate = scope_type in (ScopeType.WORKSPACE, ScopeType.TEAM, ScopeType.PROCESS)
    if aggregate and not (acl.is_admin or acl.manager_all or acl.can("habit.view_team")):
        raise Forbidden(
            "forbidden", "missing permission 'habit.view_team'", details={"permission": "habit.view_team"}
        )
    if scope_type == ScopeType.WORKSPACE:
        return ws.id
    if scope_type == ScopeType.PROJECT:
        p: m.Project | None = await s.scalar(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == ws.id,
                    (m.Project.key == scope_id.upper()) | (m.Project.id == scope_id),
                ),
                acl,
                m.Project,
            )
        )
        if p is None:
            raise NotFound("not_found", "project not found")
        return p.id
    if scope_type == ScopeType.PERSON:
        handle = scope_id if scope_id.startswith("@") else "@" + scope_id
        row = await s.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == ws.id,
                (m.WorkspaceMember.handle == handle) | (m.WorkspaceMember.person_id == scope_id),
            )
        )
        if row is None or not (
            row.person_id == person_id
            or acl.is_admin
            or acl.manager_all
            or row.person_id in acl.managed_ids
            or acl.can("habit.view_team")
        ):
            raise NotFound("not_found", "person not found")
        return row.person_id
    if scope_type == ScopeType.TEAM:
        t = await s.scalar(
            select(m.Team).where(
                m.Team.workspace_id == ws.id, (m.Team.slug == scope_id) | (m.Team.id == scope_id)
            )
        )
        if t is None:
            raise NotFound("not_found", "team not found")
        return t.id
    if scope_type == ScopeType.PROCESS:
        return scope_id if ":" in scope_id else f"{ws.id}:{scope_id}"
    return scope_id


async def metric_series(
    uow: UnitOfWork,
    ws: m.Workspace,
    acl: AclContext,
    person_id: str,
    *,
    scope_type: ScopeType,
    scope_id: str,
    from_: date | None,
    to: date | None,
    scheme: str | None,
) -> list[dict[str, Any]]:
    """Snapshots of one scope (optionally per weight scheme) in date order.

    Wellbeing habit scores are dropped unless the viewer is the person or their line manager (object model §13.3).
    """
    s = uow.session
    sid = await _resolve_scope(uow, ws, acl, person_id, scope_type, scope_id)
    sch = None
    if scheme:
        sch = await s.scalar(
            select(m.WeightScheme.id).where(
                m.WeightScheme.workspace_id == ws.id,
                (m.WeightScheme.id == scheme) | (m.WeightScheme.name == scheme),
            )
        )
        if sch is None:
            raise Invalid("unknown_scheme", f"unknown weight scheme {scheme!r}", pointer="/scheme")
    stmt = select(m.MetricSnapshot).where(
        m.MetricSnapshot.scope_type == scope_type,
        m.MetricSnapshot.scope_id == sid,
        m.MetricSnapshot.weight_scheme_id.is_(None)
        if sch is None
        else m.MetricSnapshot.weight_scheme_id == sch,
    )
    if from_:
        stmt = stmt.where(m.MetricSnapshot.date >= from_)
    if to:
        stmt = stmt.where(m.MetricSnapshot.date <= to)
    rows = (await s.scalars(stmt.order_by(m.MetricSnapshot.date))).all()
    own_view = scope_type == ScopeType.PERSON and (sid == person_id or sid in acl.managed_ids)
    wellbeing = (
        set()
        if own_view
        else set(
            (
                await s.scalars(
                    select(m.HabitDefinition.key).where(
                        m.HabitDefinition.category == PolicyCategory.WELLBEING
                    )
                )
            ).all()
        )
    )

    def clean(metrics: dict[str, Any]) -> dict[str, Any]:
        hs = metrics.get("habit_scores")
        if wellbeing and isinstance(hs, dict):
            metrics = {**metrics, "habit_scores": {k: v for k, v in hs.items() if k not in wellbeing}}
        return metrics

    return [
        {
            "date": r.date,
            "scope_type": r.scope_type.value,
            "scope_id": r.scope_id,
            "weight_scheme_id": r.weight_scheme_id,
            "metrics": clean(r.metrics),
        }
        for r in rows
    ]
