"""Approvals: any_one | all | quorum (object model §7), gating stage entry and milestone completion."""

from __future__ import annotations

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import ApprovalPolicy, ApprovalStatus, Decision, EventSource, ProjectRole, TargetType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, Forbidden, Invalid, NotFound
from tt_server.domain.overlay import process as proc


async def _approver_ids(uow: UnitOfWork, project: m.Project) -> list[str]:
    s = uow.session
    ids: list[str] = []
    for handle in (project.settings or {}).get("approvers", []):
        pid = await s.scalar(
            select(m.WorkspaceMember.person_id).where(
                m.WorkspaceMember.workspace_id == project.workspace_id, m.WorkspaceMember.handle == handle
            )
        )
        if pid:
            ids.append(pid)
    ids += list(
        (
            await s.scalars(
                select(m.ProjectMember.person_id).where(
                    m.ProjectMember.project_id == project.id,
                    m.ProjectMember.role.in_([ProjectRole.OWNER, ProjectRole.MANAGER]),
                )
            )
        ).all()
    )
    ids.append(project.owner_id)
    return list(dict.fromkeys(ids))


async def request(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    project_id: str,
    target_type: TargetType,
    target_id: str,
    title: str,
    requested_by: str,
) -> m.Approval:
    project = await uow.session.get(m.Project, project_id)
    assert project is not None  # noqa: S101
    cfg = project.settings or {}
    policy = ApprovalPolicy(cfg.get("approval_policy", "any_one"))
    approval = m.Approval(
        id=new_id(),
        project_id=project.id,
        target_type=target_type,
        target_id=target_id,
        title=title,
        requested_by=requested_by,
        policy=policy,
        quorum_n=int(cfg.get("approval_quorum", 2)) if policy == ApprovalPolicy.QUORUM else None,
    )
    uow.session.add(approval)
    await uow.session.flush()
    for pid in await _approver_ids(uow, project):
        uow.session.add(m.ApprovalDecision(id=new_id(), approval_id=approval.id, approver_id=pid))
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=requested_by,
        entity_type=target_type,
        entity_id=target_id,
        event_type="approval.requested",
        payload={"after": {"approval_id": approval.id, "policy": policy.value}},
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    return approval


def evaluate(policy: ApprovalPolicy, quorum_n: int | None, decisions: list[Decision]) -> ApprovalStatus:
    approved = sum(d == Decision.APPROVED for d in decisions)
    rejected = sum(d == Decision.REJECTED for d in decisions)
    pending = sum(d == Decision.PENDING for d in decisions)
    total = len(decisions)
    if policy == ApprovalPolicy.ANY_ONE:
        if approved:
            return ApprovalStatus.APPROVED
        return ApprovalStatus.REJECTED if total and pending == 0 and rejected else ApprovalStatus.PENDING
    if policy == ApprovalPolicy.ALL:
        if rejected:
            return ApprovalStatus.REJECTED
        return ApprovalStatus.APPROVED if total and approved == total else ApprovalStatus.PENDING
    need = min(quorum_n or 1, total) if total else 1
    if approved >= need:
        return ApprovalStatus.APPROVED
    return ApprovalStatus.REJECTED if approved + pending < need else ApprovalStatus.PENDING


async def decide(
    uow: UnitOfWork,
    ws: m.Workspace,
    approval: m.Approval,
    *,
    approver_id: str,
    decision: Decision,
    comment: str | None = None,
) -> m.Approval:
    s = uow.session
    if approval.status != ApprovalStatus.PENDING:
        raise Conflict("approval_resolved", f"approval already {approval.status.value}")
    if decision == Decision.PENDING:
        raise Invalid(
            "invalid_decision", "decision must be approved, rejected or abstained", pointer="/decision"
        )
    rows = list(
        (
            await s.scalars(select(m.ApprovalDecision).where(m.ApprovalDecision.approval_id == approval.id))
        ).all()
    )
    mine = next((r for r in rows if r.approver_id == approver_id), None)
    if mine is None:
        raise Forbidden("not_an_approver", "you are not an approver of this request")
    mine.decision, mine.comment, mine.decided_at = decision, comment, utcnow()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=approver_id,
        entity_type=approval.target_type,
        entity_id=approval.target_id,
        event_type="approval.decided",
        payload={"after": {"approval_id": approval.id, "decision": decision.value}},
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    effective: list[Decision] = [r.decision for r in rows if r.decision != Decision.ABSTAINED]
    status = evaluate(approval.policy, approval.quorum_n, effective)
    if status != ApprovalStatus.PENDING:
        approval.status, approval.resolved_at = status, utcnow()
        ev = emit(
            uow,
            workspace_id=ws.id,
            actor_id=approver_id,
            entity_type=approval.target_type,
            entity_id=approval.target_id,
            event_type="approval.resolved",
            payload={"after": {"approval_id": approval.id, "status": status.value}},
            source=EventSource.OVERLAY,
            correlation_id=uow.context.get("correlation_id"),
        )
        if status == ApprovalStatus.REJECTED and approval.target_type == TargetType.STAGE_PLACEMENT:
            await _roll_back_placement(uow, ws, approval, approver_id, ev.id)
    return approval


async def _roll_back_placement(
    uow: UnitOfWork, ws: m.Workspace, approval: m.Approval, by: str, trigger_event_id: str
) -> None:

    s = uow.session
    placement = await s.get(m.StagePlacement, approval.target_id)
    if placement is None or placement.exited_at is not None:
        return
    previous = await s.scalar(
        select(m.StagePlacement)
        .where(
            m.StagePlacement.target_type == placement.target_type,
            m.StagePlacement.target_id == placement.target_id,
            m.StagePlacement.process_id == placement.process_id,
            m.StagePlacement.exited_at == placement.entered_at,
            m.StagePlacement.id != placement.id,
        )
        .order_by(m.StagePlacement.entered_at.desc())
        .limit(1)
    )
    if previous is None:
        return
    process = await s.get(m.Process, placement.process_id)
    stage = await s.get(m.Stage, previous.stage_id)
    assert process and stage  # noqa: S101
    await proc.place(
        uow,
        ws,
        target_type=placement.target_type,
        target_id=placement.target_id,
        process=process,
        stage=stage,
        moved_by=by,
        reason="approval rejected",
        source=EventSource.OVERLAY,
        strict=False,
        correlation_id=trigger_event_id,
    )


async def is_approved(session: object, approval_id: str | None) -> bool:
    if approval_id is None:
        return True
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)  # noqa: S101
    a = await session.get(m.Approval, approval_id)
    return a is not None and a.status == ApprovalStatus.APPROVED


async def list_approvals(
    uow: UnitOfWork, ws_id: str, acl: AclContext, person_id: str, *, status: ApprovalStatus | None, mine: bool
) -> list[m.Approval]:
    """Approvals on visible projects, newest first; `mine` keeps those awaiting the person's decision."""
    stmt = (
        select(m.Approval)
        .join(m.Project, m.Project.id == m.Approval.project_id)
        .where(m.Project.workspace_id == ws_id, m.Approval.deleted_at.is_(None))
    )
    stmt = visible(stmt, acl, m.Project)
    if status:
        stmt = stmt.where(m.Approval.status == status)
    if mine:
        stmt = stmt.join(m.ApprovalDecision, m.ApprovalDecision.approval_id == m.Approval.id).where(
            m.ApprovalDecision.approver_id == person_id, m.ApprovalDecision.decision == Decision.PENDING
        )
    return list((await uow.session.scalars(stmt.order_by(m.Approval.requested_at.desc()))).all())


async def get_approval(uow: UnitOfWork, ws_id: str, aid: str) -> m.Approval:
    """An approval of the workspace by id; 404 when absent."""
    a = await uow.session.scalar(
        select(m.Approval)
        .join(m.Project, m.Project.id == m.Approval.project_id)
        .where(m.Approval.id == aid, m.Project.workspace_id == ws_id)
    )
    if a is None:
        raise NotFound("not_found", "approval not found")
    return a


async def project_of(uow: UnitOfWork, approval: m.Approval) -> m.Project:
    """The project an approval belongs to."""
    p = await uow.session.get(m.Project, approval.project_id)
    if p is None:
        raise NotFound("not_found", "project not found")
    return p
