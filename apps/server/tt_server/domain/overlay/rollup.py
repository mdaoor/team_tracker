"""Roll-ups and provenance (object model §6 roll-ups, UX §3.4)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import StateCategory, TargetType

from tt_server.db import models as m
from tt_server.domain.overlay.risks import open_risk_score

DIMENSION_FIELDS = {
    "stage": "stage",
    "product": "product",
    "project": "project",
    "team": "team",
    "milestone": "milestone",
    "iteration": "iteration",
    "priority": "priority",
}


async def rollup(session: AsyncSession, root: m.WorkItem) -> dict[str, Any]:
    """Computed on read for the subtree of `root` (<= 500 items; larger trees need the Phase 12 job)."""
    rows = (
        await session.execute(
            select(m.WorkItem, m.WorkflowState.category)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(m.WorkItem.path.op("<@")(root.path), m.WorkItem.deleted_at.is_(None))
            .limit(501)
        )
    ).all()
    truncated = len(rows) > 500
    rows = rows[:500]
    ids = [it.id for it, _ in rows]
    leaves = [(it, c) for it, c in rows if not any(o.parent_id == it.id for o, _ in rows)] or rows
    total_weight = sum((Decimal(it.weight) for it, _ in leaves), Decimal(0))
    done_weight = sum((Decimal(it.weight) for it, c in leaves if c == StateCategory.DONE), Decimal(0))
    remaining = sum(
        (
            Decimal(it.remaining_value if it.remaining_value is not None else (it.estimate_value or 0))
            for it, c in leaves
            if c != StateCategory.DONE
        ),
        Decimal(0),
    )
    minutes = await session.scalar(
        select(func.coalesce(func.sum(m.TimeEntry.minutes), 0)).where(m.TimeEntry.work_item_id.in_(ids))
    )
    stage_rows = (
        await session.execute(
            select(m.Stage.key, func.count())
            .select_from(m.StagePlacement)
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .where(
                m.StagePlacement.target_type == TargetType.WORK_ITEM,
                m.StagePlacement.target_id.in_(ids),
                m.StagePlacement.exited_at.is_(None),
            )
            .group_by(m.Stage.key)
        )
    ).all()
    return {
        "item_count": len(rows),
        "truncated": truncated,
        "progress_pct": round(float(done_weight / total_weight) * 100) if total_weight else 0,
        "remaining_estimate": str(remaining),
        "sum_weight": str(total_weight),
        "open_risk_score": await open_risk_score(session, ids),
        "stage_distribution": {k: n for k, n in stage_rows},
        "hours_logged": round((minutes or 0) / 60, 2),
    }


async def provenance(session: AsyncSession, item: m.WorkItem) -> dict[str, Any]:
    """Per overlay field: {value, set_by, set_at, source, inherited_from} (UX §3.4 'who set this')."""
    out: dict[str, Any] = {}
    rows = (
        await session.execute(
            select(m.Dimension.key, m.DimensionValue.key, m.Classification)
            .join(m.Classification, m.Classification.dimension_id == m.Dimension.id)
            .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
            .where(
                m.Classification.target_type == TargetType.WORK_ITEM,
                m.Classification.target_id == item.id,
                m.Classification.deleted_at.is_(None),
            )
        )
    ).all()
    for dim_key, value_key, c in rows:
        out.setdefault(dim_key, []).append(
            {
                "value": value_key,
                "set_by": c.set_by,
                "set_at": c.set_at.isoformat(),
                "source": c.source.value,
                "confirmed": c.is_confirmed,
                "inherited_from": {"type": c.inherited_from_type.value, "id": c.inherited_from_id}
                if c.inherited_from_type
                else None,
            }
        )
    for key, wanted in (
        ("priority", ("item.priority_changed",)),
        ("impact", ("item.impact_changed",)),
        ("assignee", ("item.updated",)),
    ):
        ev = await session.scalar(
            select(m.Event)
            .where(
                m.Event.entity_type == TargetType.WORK_ITEM,
                m.Event.entity_id == item.id,
                m.Event.event_type.in_(wanted),
            )
            .order_by(m.Event.occurred_at.desc(), m.Event.id.desc())
            .limit(1)
        )
        if ev is not None and key not in out:
            out[key] = [
                {
                    "value": (ev.payload.get("after") or {}).get(key),
                    "set_by": ev.actor_id,
                    "set_at": ev.occurred_at.isoformat(),
                    "source": ev.source.value,
                    "inherited_from": None,
                }
            ]
    out["priority_source"] = item.priority_source.value
    return out
