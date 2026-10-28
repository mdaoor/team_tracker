"""Target enumeration for policy evaluation: scope predicates, candidate item statements and the evaluable `Ref`s (Phase 8 section 8.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import tt_cel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_filter import parse as parse_filter
from tt_schema.enums import ActivityStatus, DailyLogStatus, ProjectKind, ScopeType, TargetType

from tt_server.acl.context import AclContext
from tt_server.db import models as m
from tt_server.governance import context as ctxmod
from tt_server.governance import facts as factmod
from tt_server.governance import service
from tt_server.query.compile import QueryCtx, compile_filter


@dataclass
class Ref:
    """One evaluable target before its context is built."""

    type: TargetType
    id: str
    item_id: str | None = None
    activity: m.Activity | None = None
    log: m.DailyLog | None = None
    person_id: str | None = None
    project_id: str | None = None


async def template_for(s: AsyncSession, policy: m.Policy) -> tt_cel.Template | None:
    """The stored template a tier-1 policy was built from (carries `detail` and `grouping`); raw-CEL policies have none."""
    if not policy.condition_template_id:
        return None
    row = await s.get(m.PolicyTemplate, policy.condition_template_id)
    return service.template_from_row(row) if row is not None else None


async def team_scope_ids(s: AsyncSession, ws_id: str, team_id: str) -> set[str]:
    """The team and all its descendant teams."""
    teams = (
        await s.execute(select(m.Team.id, m.Team.parent_team_id).where(m.Team.workspace_id == ws_id))
    ).all()
    out, frontier = {team_id}, {team_id}
    while frontier:
        frontier = {t for t, p in teams if p in frontier} - out
        out |= frontier
    return out


async def _scope_pred(s: AsyncSession, ws: m.Workspace, policy: m.Policy) -> Any:
    """SQL predicate over `work_items` for the policy's scope (None = whole workspace)."""
    st, sid = policy.scope_type, policy.scope_id
    if st in (ScopeType.WORKSPACE, ScopeType.NONE) or not sid:
        return None
    if st == ScopeType.PROJECT:
        return m.WorkItem.project_id == sid
    if st == ScopeType.TEAM:
        return m.WorkItem.team_id.in_(await team_scope_ids(s, ws.id, sid))
    if st == ScopeType.PRODUCT:
        links = select(m.ProductLink.target_id).where(
            m.ProductLink.product_id == sid,
            m.ProductLink.target_type == TargetType.PROJECT,
            m.ProductLink.deleted_at.is_(None),
        )
        return m.WorkItem.project_id.in_(links)
    if st == ScopeType.PROCESS:
        return m.WorkItem.id.in_(
            select(m.StagePlacement.target_id).where(
                m.StagePlacement.process_id == sid,
                m.StagePlacement.target_type == TargetType.WORK_ITEM,
                m.StagePlacement.exited_at.is_(None),
            )
        )
    return None


def system_qc(s: AsyncSession, ws: m.Workspace, now: datetime) -> QueryCtx:
    """Query context with admin rights for policy evaluation, which must see everything."""
    acl = AclContext(ws_id=ws.id, person_id="system", is_admin=True)
    return QueryCtx(session=s, ws_id=ws.id, acl=acl, person_id="system", handle="@system", tz="UTC", now=now)


async def candidate_statement(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, now: datetime, only: list[str] | None = None
) -> Any:
    """`SELECT id` of the items in scope that pass `candidate_filter`: the filter and the scope are SQL, not Python (Phase 6 compiler)."""
    cf = policy.candidate_filter or {}
    stmt = (
        select(m.WorkItem.id)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(
            m.Project.workspace_id == ws.id,
            m.WorkItem.deleted_at.is_(None),
            m.WorkItem.is_template.is_(False),
        )
    )
    if not cf.get("include_inbox"):
        stmt = stmt.where(m.Project.kind != ProjectKind.INBOX)
    if cf:
        stmt = stmt.where(await compile_filter(parse_filter(cf), system_qc(s, ws, now)))
    sp = await _scope_pred(s, ws, policy)
    if sp is not None:
        stmt = stmt.where(sp)
    if only is not None:
        stmt = stmt.where(m.WorkItem.id.in_(only))
    return stmt.order_by(m.WorkItem.key)


async def candidate_item_ids(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, now: datetime, only: list[str] | None = None
) -> list[str]:
    """Item ids in scope that pass `candidate_filter`. Governance is a system actor: no ACL."""
    return list((await s.scalars(await candidate_statement(s, ws, policy, now, only))).all())


async def enumerate_targets(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, now: datetime, only: list[Ref] | None
) -> list[Ref]:
    """List the targets a policy applies to, or only those given."""
    t = policy.target
    if only is not None and t in (
        TargetType.ACTIVITY,
        TargetType.DAILY_LOG,
        TargetType.PERSON,
        TargetType.PROJECT,
    ):
        return [r for r in only if r.type == t]
    if t in (TargetType.WORK_ITEM, TargetType.STAGE_PLACEMENT):
        wanted = [r.item_id for r in only if r.item_id] if only is not None else None
        ids = await candidate_item_ids(s, ws, policy, now, wanted)
        if t == TargetType.WORK_ITEM:
            return [Ref(TargetType.WORK_ITEM, i, item_id=i) for i in ids]
        out: list[Ref] = []
        for k in range(0, len(ids), 5000):
            for pid, tid in (
                await s.execute(
                    select(m.StagePlacement.id, m.StagePlacement.target_id).where(
                        m.StagePlacement.target_type == TargetType.WORK_ITEM,
                        m.StagePlacement.target_id.in_(ids[k : k + 5000]),
                        m.StagePlacement.exited_at.is_(None),
                    )
                )
            ).all():
                out.append(Ref(TargetType.STAGE_PLACEMENT, pid, item_id=tid))
        return out
    if t == TargetType.ACTIVITY:
        acts = (
            await s.scalars(
                select(m.Activity)
                .join(m.Project, m.Project.id == m.Activity.project_id)
                .where(m.Project.workspace_id == ws.id, m.Activity.status == ActivityStatus.ACTIVE)
            )
        ).all()
        return [
            Ref(TargetType.ACTIVITY, a.id, item_id=a.work_item_id, activity=a, person_id=a.person_id)
            for a in acts
        ]
    if t == TargetType.PERSON:
        rows = (
            await s.scalars(
                select(m.WorkspaceMember).where(
                    m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.deleted_at.is_(None)
                )
            )
        ).all()
        return [Ref(TargetType.PERSON, r.person_id, person_id=r.person_id) for r in rows]
    if t == TargetType.DAILY_LOG:
        logs = (
            await s.scalars(
                select(m.DailyLog).where(
                    m.DailyLog.workspace_id == ws.id, m.DailyLog.status == DailyLogStatus.DRAFT
                )
            )
        ).all()
        return [Ref(TargetType.DAILY_LOG, d.id, log=d, person_id=d.person_id) for d in logs]
    if t == TargetType.PROJECT:
        pj = (
            await s.scalars(
                select(m.Project).where(
                    m.Project.workspace_id == ws.id,
                    m.Project.deleted_at.is_(None),
                    m.Project.kind == ProjectKind.STANDARD,
                )
            )
        ).all()
        return [Ref(TargetType.PROJECT, p.id, project_id=p.id) for p in pj]
    return []


async def _project_roots(s: AsyncSession, ws_id: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    teams = {
        t.id: t.slug for t in (await s.scalars(select(m.Team).where(m.Team.workspace_id == ws_id))).all()
    }
    prod = {
        pid: key
        for pid, key in (
            await s.execute(
                select(m.ProductLink.target_id, m.Product.key)
                .join(m.Product, m.Product.id == m.ProductLink.product_id)
                .where(
                    m.ProductLink.target_type == TargetType.PROJECT,
                    m.ProductLink.deleted_at.is_(None),
                    m.ProductLink.target_id.in_(ids),
                )
            )
        ).all()
    }
    out = {}
    for p in (await s.scalars(select(m.Project).where(m.Project.id.in_(ids)))).all():
        out[p.id] = {
            "id": p.id, "key": p.key, "methodology": p.methodology.value, "status": p.status.value,
            "planned_end": factmod.date_ts(p.planned_end), "team": teams.get(p.team_id or ""), "product": prod.get(p.id),
            "_team_id": p.team_id,
        }  # fmt: skip
    return out


async def _person_roots(s: AsyncSession, ws_id: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    primary: dict[str, str] = {}
    teams = {
        t.id: t.slug for t in (await s.scalars(select(m.Team).where(m.Team.workspace_id == ws_id))).all()
    }
    for pid, tid in (
        await s.execute(
            select(m.Membership.person_id, m.Membership.team_id)
            .where(
                m.Membership.person_id.in_(ids),
                m.Membership.left_at.is_(None),
                m.Membership.deleted_at.is_(None),
            )
            .order_by(m.Membership.joined_at.desc())
        )
    ).all():
        primary[pid] = teams.get(tid, "")
    out = {}
    for mem, person in (
        await s.execute(
            select(m.WorkspaceMember, m.Person)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.person_id.in_(ids))
        )
    ).all():
        out[person.id] = ctxmod.person_root(mem, person, primary.get(person.id))
    return out
