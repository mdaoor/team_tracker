"""Turn evaluation matches into violations: grace period, de-duplication, auto-resolve, notifications, auto-actions (§8.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, PolicyCategory, TargetType, ViolationStatus

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import propagation
from tt_server.governance import notify as notifier
from tt_server.governance import service
from tt_server.governance.engine import EvalResult, Match

OPEN = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


def parse_duration(text: str | None) -> timedelta:
    """`"2d"`, `"36h"`, `"90m"`, `"1w"`; None/empty = no grace."""
    if not text:
        return timedelta(0)
    n, unit = int(text[:-1]), text[-1]
    return {
        "d": timedelta(days=n),
        "h": timedelta(hours=n),
        "m": timedelta(minutes=n),
        "w": timedelta(weeks=n),
    }[unit]


@dataclass
class Applied:
    opened: list[m.Violation] = field(default_factory=list)
    resolved: list[m.Violation] = field(default_factory=list)
    pending: int = 0  # matches still inside their grace period
    notified: int = 0
    actions: int = 0


async def _product_of(s: AsyncSession, project_ids: set[str]) -> dict[str, str]:
    if not project_ids:
        return {}
    return {
        pid: prod
        for pid, prod in (
            await s.execute(
                select(m.ProductLink.target_id, m.ProductLink.product_id).where(
                    m.ProductLink.target_type == TargetType.PROJECT,
                    m.ProductLink.target_id.in_(project_ids),
                    m.ProductLink.deleted_at.is_(None),
                )
            )
        ).all()
    }


async def apply(
    uow: UnitOfWork, ws: m.Workspace, policy: m.Policy, res: EvalResult, *, now: datetime | None = None
) -> Applied:
    """Reconcile `res` with the open violations of `policy`. Caller commits."""
    s = uow.session
    now = now or utcnow()
    sys = await service.ensure_system(uow)
    uow.context.update(actor_id=sys.id)
    out = Applied()
    grace = parse_duration(policy.grace_period)
    matched = {(mt.target_type.value, mt.target_id): mt for mt in res.matches}
    open_rows = {
        (v.target_type.value, v.target_id): v
        for v in (
            await s.scalars(
                select(m.Violation).where(
                    m.Violation.policy_id == policy.id,
                    m.Violation.status.in_(OPEN),
                    m.Violation.deleted_at.is_(None),
                )
            )
        ).all()
    }
    # ---- match bookkeeping (Core statements: it is bookkeeping, not a projection) ---------------------------------------
    # `first_matched_at` drives both the grace period and the waive/false-positive suppression: a waived target stays quiet for as long as
    # the same condition keeps holding, and fires again only after it has cleared in between.
    first: dict[tuple[str, str], datetime] = {}
    for tt, tid, at in (
        await s.execute(
            select(
                m.PolicyMatchState.target_type,
                m.PolicyMatchState.target_id,
                m.PolicyMatchState.first_matched_at,
            ).where(m.PolicyMatchState.policy_id == policy.id)
        )
    ).all():
        first[(tt, tid)] = at
    if matched:
        fresh = [k for k in matched if k not in first]
        for i in range(0, len(fresh), 4000):  # bind-parameter limit: 5 per row
            await s.execute(
                pg_insert(m.PolicyMatchState)
                .values(
                    [
                        {
                            "id": new_id(),
                            "policy_id": policy.id,
                            "target_type": k[0],
                            "target_id": k[1],
                            "first_matched_at": now,
                        }
                        for k in fresh[i : i + 4000]
                    ]
                )
                .on_conflict_do_nothing()
            )
            first.update({k: now for k in fresh})
    quiet: dict[tuple[str, str], datetime] = {}
    if matched:
        for tt, tid, at in (
            await s.execute(
                select(m.Violation.target_type, m.Violation.target_id, func.max(m.Violation.updated_at))
                .where(
                    m.Violation.policy_id == policy.id,
                    m.Violation.status.in_([ViolationStatus.WAIVED, ViolationStatus.FALSE_POSITIVE]),
                )
                .group_by(m.Violation.target_type, m.Violation.target_id)
            )
        ).all():
            quiet[(tt.value, tid)] = at
    # stop tracking what no longer matches
    gone_state = [
        k for k in (res.evaluated if not res.sweep else set(first) | res.evaluated) if k not in matched
    ]
    for i in range(0, len(gone_state), 10000):
        await s.execute(
            delete(m.PolicyMatchState).where(
                m.PolicyMatchState.policy_id == policy.id,
                sa.tuple_(m.PolicyMatchState.target_type, m.PolicyMatchState.target_id).in_(
                    gone_state[i : i + 10000]
                ),
            )
        )
    # ---- open ---------------------------------------------------------------------------------------------------
    new: list[Match] = []
    for key, mt in matched.items():
        if key in open_rows:
            continue  # already open: nothing to do (idempotent)
        if key in quiet and first.get(key, now) <= quiet[key]:
            continue  # waived / false positive, and the condition has held ever since
        if grace and now - first.get(key, now) < grace:
            out.pending += 1
            continue
        new.append(mt)
    products = await _product_of(s, {mt.project_id for mt in new if mt.project_id})
    for mt in new:
        v = m.Violation(
            id=new_id(), workspace_id=ws.id, policy_id=policy.id, target_type=mt.target_type, target_id=mt.target_id,
            severity=policy.severity, category=policy.category, person_id=mt.person_id, team_id=mt.team_id,
            project_id=mt.project_id, product_id=products.get(mt.project_id or ""), detected_at=now,
            evidence={**mt.evidence, "policy": policy.key}, status=ViolationStatus.OPEN,
        )  # fmt: skip
        s.add(v)
        await s.flush()
        emit(
            uow, workspace_id=ws.id, actor_id=sys.id, entity_type=mt.target_type, entity_id=mt.target_id, event_type="violation.opened",
            payload={"after": {"violation_id": v.id, "policy": policy.key, "severity": policy.severity.value, "detail": mt.detail}},
            source=EventSource.RULE, occurred_at=now,
        )  # fmt: skip
        out.opened.append(v)
        out.notified += await notifier.notify(uow, ws, policy, v, now=now)
        out.actions += await _auto_action(uow, ws, policy, v, mt, now)
    # ---- auto-resolve: the condition cleared ----------------------------------------------------------------------
    for key, v in open_rows.items():
        if key in matched:
            continue
        if res.sweep or key in res.evaluated:
            v.status, v.resolved_at, v.resolution_note = ViolationStatus.RESOLVED, now, "condition cleared"
            emit(
                uow, workspace_id=ws.id, actor_id=sys.id, entity_type=v.target_type, entity_id=v.target_id, event_type="violation.auto_resolved",
                payload={"before": {"status": "open"}, "after": {"violation_id": v.id, "status": "resolved", "policy": policy.key}},
                source=EventSource.RULE, occurred_at=now,
            )  # fmt: skip
            out.resolved.append(v)
    return out


async def _auto_action(
    uow: UnitOfWork, ws: m.Workspace, policy: m.Policy, v: m.Violation, mt: Match, now: datetime
) -> int:
    """`{"set": {"priority": "high"}}`, `{"move_stage": "test"}`, `{"create_item": {...}}`; all audited as `source=rule` events."""
    act = policy.auto_action or {}
    if not act:
        return 0
    s = uow.session
    n = 0
    item = (
        await s.get(
            m.WorkItem,
            mt.evidence.get("item_id") or (mt.target_id if mt.target_type == TargetType.WORK_ITEM else ""),
        )
        if (mt.evidence.get("item_id") or mt.target_type == TargetType.WORK_ITEM)
        else None
    )
    if item is None and mt.evidence.get("item"):
        item = await s.scalar(
            select(m.WorkItem)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .where(m.Project.workspace_id == ws.id, m.WorkItem.key == mt.evidence["item"])
        )
    sys_id = uow.context["actor_id"]
    if "set" in act and item is not None:
        from tt_schema.enums import Priority

        if "priority" in act["set"] and item.priority.value != act["set"]["priority"]:
            await propagation.set_item_priority(
                uow, ws, item, Priority(act["set"]["priority"]), actor=sys_id, manual=False
            )
            n += 1
    if "move_stage" in act and item is not None:
        p = await proc.get_process(s, ws.id, None)
        stage = await proc.get_stage(s, p, act["move_stage"])
        await proc.place(
            uow,
            ws,
            target_type=TargetType.WORK_ITEM,
            target_id=item.id,
            process=p,
            stage=stage,
            moved_by=sys_id,
            reason=f"policy {policy.key}",
            source=EventSource.RULE,
        )
        n += 1
    if "create_item" in act:
        spec = act["create_item"]
        project = await s.scalar(
            select(m.Project).where(
                m.Project.workspace_id == ws.id, m.Project.key == str(spec.get("project", "")).upper()
            )
        ) or (await s.get(m.Project, v.project_id) if v.project_id else None)
        if project is not None:
            created = await items.create_item(
                uow,
                ws,
                project,
                title=str(spec.get("title", f"Follow up: {policy.name}")),
                reporter_id=policy.owner_id,
                assignee_id=policy.owner_id,
            )
            emit(
                uow, workspace_id=ws.id, actor_id=sys_id, entity_type=TargetType.WORK_ITEM, entity_id=created.id, event_type="item.created",
                payload={"after": {"title": created.title, "cause": f"policy:{policy.key}", "violation_id": v.id}}, source=EventSource.RULE, occurred_at=now,
            )  # fmt: skip
            n += 1
    if n:
        emit(
            uow, workspace_id=ws.id, actor_id=sys_id, entity_type=v.target_type, entity_id=v.target_id, event_type="policy.auto_action",
            payload={"after": {"policy": policy.key, "violation_id": v.id, "action": act}}, source=EventSource.RULE, occurred_at=now,
        )  # fmt: skip
    return n


__all__ = ["Applied", "PolicyCategory", "apply", "parse_duration"]
