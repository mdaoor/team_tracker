"""Violation notifications (Phase 8 §8.4): who hears about it, once per violation, recipient and day."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tt_schema.base import new_id
from tt_schema.enums import EventSource, PolicyCategory, ReportingKind, ScopeType, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit


async def recipients(uow: UnitOfWork, ws: m.Workspace, policy: m.Policy, v: m.Violation) -> set[str]:
    """`notify = {person: true, reporting_line: ["line"], roles: ["manager"]}`.

    Wellbeing violations only ever go to the subject and their line managers, whatever `roles` says (privacy rule, §13.3).
    """
    s = uow.session
    cfg = policy.notify or {}
    out: set[str] = set()
    wellbeing = v.category == PolicyCategory.WELLBEING
    if cfg.get("person") and v.person_id:
        out.add(v.person_id)
    if v.person_id and cfg.get("reporting_line"):
        kinds = [ReportingKind(k) for k in cfg["reporting_line"]]
        out |= set(
            (
                await s.scalars(
                    select(m.ReportingLine.manager_id).where(
                        m.ReportingLine.person_id == v.person_id,
                        m.ReportingLine.kind.in_(kinds),
                        m.ReportingLine.deleted_at.is_(None),
                    )
                )
            ).all()
        )
    if cfg.get("roles") and not wellbeing:
        for row in (
            await s.execute(
                select(m.RoleAssignment.person_id, m.RoleAssignment.scope_type, m.RoleAssignment.scope_id)
                .join(m.Role, m.Role.id == m.RoleAssignment.role_id)
                .where(
                    m.Role.key.in_(cfg["roles"]),
                    m.RoleAssignment.workspace_id == ws.id,
                    m.RoleAssignment.deleted_at.is_(None),
                )
            )
        ).all():
            pid, st, sid = row
            if (
                (st == ScopeType.WORKSPACE and sid == ws.id)
                or (st == ScopeType.PROJECT and sid == v.project_id)
                or (st == ScopeType.TEAM and sid == v.team_id)
            ):
                out.add(pid)
    return out


async def notify(uow: UnitOfWork, ws: m.Workspace, policy: m.Policy, v: m.Violation, *, now: datetime) -> int:
    """Send in-app notifications for a violation to its recipients, once each; returns how many were created."""
    s = uow.session
    n = 0
    detail = (v.evidence or {}).get("detail") or ""
    for pid in sorted(await recipients(uow, ws, policy, v)):
        res = await s.execute(
            pg_insert(m.Notification)
            .values(
                id=new_id(),
                workspace_id=ws.id,
                person_id=pid,
                violation_id=v.id,
                kind="violation",
                title=f"{policy.name}",
                body=detail,
                channel=(policy.notify or {}).get("channel", "in_app"),
                day=now.date(),
            )
            .on_conflict_do_nothing(constraint="uq_notifications_dedupe")
            .returning(m.Notification.id)
        )
        if res.scalar_one_or_none():
            n += 1
            emit(
                uow, workspace_id=ws.id, actor_id=uow.context["actor_id"], entity_type=TargetType.PERSON, entity_id=pid, event_type="notification.sent",
                payload={"after": {"violation_id": v.id, "policy": policy.key, "channel": "in_app"}}, source=EventSource.RULE, occurred_at=now,
            )  # fmt: skip
    return n
