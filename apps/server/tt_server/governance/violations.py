"""Violation queries and lifecycle transitions behind `/violations` (Phase 8)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import PolicyCategory, Severity, ViolationStatus

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, Forbidden, Invalid, NotFound
from tt_server.domain.names import Names
from tt_server.governance import service

OPEN = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


def parse_enum(cls: Any, value: str, name: str) -> Any:
    """Parse a query-string enum member; a bad value is a 400 naming the allowed ones."""
    try:
        return cls(value)
    except ValueError as exc:
        raise Invalid(
            "invalid_" + name, f"{name} must be one of {[e.value for e in cls]}", pointer=f"/{name}"
        ) from exc


async def get_violation(uow: UnitOfWork, ws_id: str, acl: AclContext, vid: str) -> tuple[m.Violation, str]:
    """A visible violation and its policy key; invisible or absent is a 404."""
    row = (
        await uow.session.execute(
            visible(
                select(m.Violation, m.Policy.key)
                .join(m.Policy, m.Policy.id == m.Violation.policy_id)
                .where(m.Violation.id == vid, m.Violation.workspace_id == ws_id),
                acl,
                m.Violation,
            )
        )
    ).first()
    if row is None:
        raise NotFound("not_found", "violation not found")
    return row[0], row[1]


async def list_violations(
    uow: UnitOfWork,
    ws_id: str,
    acl: AclContext,
    *,
    status: str | None,
    severity: str | None,
    category: str | None,
    policy: str | None,
    person: str | None,
    project: str | None,
    target_id: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    """Visible violations, newest first, as the paged `{data, next_cursor}` envelope (offset cursor)."""
    stmt = visible(
        select(m.Violation, m.Policy.key, m.WorkspaceMember.handle)
        .join(m.Policy, m.Policy.id == m.Violation.policy_id)
        .outerjoin(
            m.WorkspaceMember,
            (m.WorkspaceMember.person_id == m.Violation.person_id)
            & (m.WorkspaceMember.workspace_id == ws_id),
        )
        .where(m.Violation.workspace_id == ws_id, m.Violation.deleted_at.is_(None)),
        acl,
        m.Violation,
    )
    if status:
        stmt = stmt.where(
            m.Violation.status.in_([parse_enum(ViolationStatus, x, "status") for x in status.split(",")])
        )
    if severity:
        stmt = stmt.where(m.Violation.severity == parse_enum(Severity, severity, "severity"))
    if category:
        stmt = stmt.where(m.Violation.category == parse_enum(PolicyCategory, category, "category"))
    if policy:
        stmt = stmt.where(m.Policy.key == policy)
    if person:
        stmt = stmt.where(m.WorkspaceMember.handle == (person if person.startswith("@") else "@" + person))
    if project:
        stmt = stmt.where(
            m.Violation.project_id
            == await uow.session.scalar(
                select(m.Project.id).where(m.Project.workspace_id == ws_id, m.Project.key == project.upper())
            )
        )
    if target_id:
        stmt = stmt.where(m.Violation.target_id == target_id)
    try:
        offset = max(0, int(cursor or 0))
    except ValueError as exc:
        raise Invalid(
            "invalid_cursor", "cursor must be a number from a previous response", pointer="/cursor"
        ) from exc
    size = min(limit, 500)
    rows = (
        await uow.session.execute(
            stmt.order_by(m.Violation.detected_at.desc(), m.Violation.id).limit(size + 1).offset(offset)
        )
    ).all()
    names = await Names.load(
        uow.session,
        ws_id,
        [(v.target_type, v.target_id) for v, _, _ in rows[:size]]
        + [("team", v.team_id) for v, _, _ in rows[:size]]
        + [("project", v.project_id) for v, _, _ in rows[:size]],
    )
    return {
        "data": [
            {
                **service.violation_view(v, pk),
                "person": h,
                "target": names.label(v.target_type, v.target_id),
                "team": names.label("team", v.team_id),
                "project": names.label("project", v.project_id),
            }
            for v, pk, h in rows[:size]
        ],
        "next_cursor": str(offset + size) if len(rows) > size else None,
    }


async def transition(
    uow: UnitOfWork,
    ws: m.Workspace,
    acl: AclContext,
    person_id: str,
    vid: str,
    new: ViolationStatus,
    *,
    perm: str | None,
    note: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Acknowledge, resolve, waive or mark a violation a false positive.

    `perm` is required unless the subject acknowledges their own violation. Only open or acknowledged violations
    move, and a violation cannot be acknowledged twice.
    """
    v, pk = await get_violation(uow, ws.id, acl, vid)
    subject = v.person_id == person_id
    if perm and not (acl.can(perm) or (new == ViolationStatus.ACKNOWLEDGED and subject)):
        raise Forbidden("forbidden", f"missing permission {perm!r}", details={"permission": perm})
    if v.status not in OPEN or (
        new == ViolationStatus.ACKNOWLEDGED and v.status == ViolationStatus.ACKNOWLEDGED
    ):
        raise Conflict("invalid_transition", f"a {v.status.value} violation cannot become {new.value}")
    before = v.status
    v.status = new
    v.updated_at = (
        utcnow()
    )  # waive / false-positive suppression compares this with when the condition began to hold
    if new == ViolationStatus.WAIVED:
        v.waived_by, v.waive_reason = person_id, reason
    elif new != ViolationStatus.ACKNOWLEDGED:
        v.resolved_at, v.resolved_by, v.resolution_note = utcnow(), person_id, note
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=person_id,
        entity_type=v.target_type,
        entity_id=v.target_id,
        event_type=f"violation.{new.value}",
        payload={
            "before": {"status": before.value},
            "after": {"violation_id": v.id, "status": new.value, "policy": pk, "note": note or reason},
        },
    )
    return service.violation_view(v, pk)
