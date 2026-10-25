"""Build the CEL evaluation context (policy context schema v1) for batches of targets.

One call loads one object graph for many targets with a handful of grouped queries; the result is a plain dict per target that
`tt_cel` converts to CEL values. Every declared root is present in every context (blank maps for what the target does not provide),
so an expression never fails on a missing root, only on a genuinely missing value (`null`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_cel.schema import OBJECTS, ROOTS
from tt_schema.enums import ActivityStatus, RiskStatus, TargetType

from tt_server.db import models as m
from tt_server.governance.facts import date_ts, day_ts


def blank(type_: str) -> Any:
    """Zero-value for a CEL type name, expanding object types recursively."""
    if type_ in OBJECTS:
        return {k: blank(v) for k, v in OBJECTS[type_].items()}
    if type_.startswith("list<"):
        return []
    if type_.startswith("map<"):
        return {}
    return None


def blank_context() -> dict[str, Any]:
    """Empty evaluation context with every root present, used to type-check conditions."""
    return {root: blank(t) for root, t in ROOTS.items() if root != "now"}


async def load_items(s: AsyncSession, ws_id: str, item_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`item` roots (with stage and placement) keyed by item id."""
    if not item_ids:
        return {}
    asg = m.WorkspaceMember
    rows = (
        await s.execute(
            select(
                m.WorkItem,
                m.Project,
                m.WorkflowState,
                m.WorkItemType.key,
                asg.handle,
                m.Team.slug,
                m.Person.id,
            )
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .outerjoin(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
            .outerjoin(asg, (asg.person_id == m.WorkItem.assignee_id) & (asg.workspace_id == ws_id))
            .outerjoin(m.Person, m.Person.id == m.WorkItem.assignee_id)
            .outerjoin(m.Team, m.Team.id == m.WorkItem.team_id)
            .where(m.WorkItem.id.in_(item_ids), m.Project.workspace_id == ws_id)
        )
    ).all()
    place: dict[str, tuple[m.StagePlacement, m.Stage, str]] = {}
    for pl_, st_, pkey_ in (
        await s.execute(
            select(m.StagePlacement, m.Stage, m.Process.key)
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .join(m.Process, m.Process.id == m.StagePlacement.process_id)
            .where(
                m.StagePlacement.target_type == TargetType.WORK_ITEM,
                m.StagePlacement.target_id.in_(item_ids),
                m.StagePlacement.exited_at.is_(None),
            )
            .order_by(
                m.StagePlacement.entered_at
            )  # the newest placement wins when an item sits in several processes
        )
    ).all():
        place[pl_.target_id] = (pl_, st_, pkey_)
    tags: dict[str, list[str]] = {}
    for tid, name in (
        await s.execute(
            select(m.TagLink.target_id, m.Tag.name)
            .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
            .where(
                m.TagLink.target_type == TargetType.WORK_ITEM,
                m.TagLink.target_id.in_(item_ids),
                m.TagLink.deleted_at.is_(None),
            )
        )
    ).all():
        tags.setdefault(tid, []).append(name)
    risks: dict[str, list[dict[str, Any]]] = {}
    for wid, rk, p, i, status in (
        await s.execute(
            select(m.RiskLink.work_item_id, m.Risk.key, m.Risk.probability, m.Risk.impact, m.Risk.status)
            .join(m.Risk, m.Risk.id == m.RiskLink.risk_id)
            .where(
                m.RiskLink.work_item_id.in_(item_ids),
                m.RiskLink.deleted_at.is_(None),
                m.Risk.deleted_at.is_(None),
                m.Risk.status != RiskStatus.CLOSED,
            )
        )
    ).all():
        risks.setdefault(wid, []).append({"key": rk, "score": p * i, "status": status.value})
    cls: dict[str, dict[str, Any]] = {}
    for tid, dk, vk, _card in (
        await s.execute(
            select(m.Classification.target_id, m.Dimension.key, m.DimensionValue.key, m.Dimension.cardinality)
            .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
            .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
            .where(
                m.Classification.target_type == TargetType.WORK_ITEM,
                m.Classification.target_id.in_(item_ids),
                m.Classification.deleted_at.is_(None),
                m.Classification.is_confirmed.is_(True),
            )
        )
    ).all():
        cur = cls.setdefault(tid, {})
        if dk in cur:
            cur[dk] = [*cur[dk], vk] if isinstance(cur[dk], list) else [cur[dk], vk]
        else:
            cur[dk] = vk
    out: dict[str, dict[str, Any]] = {}
    for it, pj, state, tkey, handle, team, person_id in rows:
        got = place.get(it.id)
        pl: m.StagePlacement | None = got[0] if got else None
        st: m.Stage | None = got[1] if got else None
        pkey: str | None = got[2] if got else None
        stage = (
            {"key": st.key, "category": st.category.value, "expected_duration_days": st.expected_duration_days, "wip_limit": st.wip_limit, "process": pkey}
            if st
            else blank("Stage")
        )  # fmt: skip
        out[it.id] = {
            "id": it.id, "key": it.key, "title": it.title,
            "project": {"id": pj.id, "key": pj.key, "methodology": pj.methodology.value, "status": pj.status.value, "planned_end": date_ts(pj.planned_end), "team": None, "product": None},
            "type": {"key": tkey}, "state": {"key": state.key, "category": state.category.value},
            "priority": it.priority.value, "priority_source": it.priority_source.value, "weight": float(it.weight or 0), "impact": it.impact,
            "estimate_value": float(it.estimate_value) if it.estimate_value is not None else None,
            "estimate_unit": it.estimate_unit.value, "remaining_value": float(it.remaining_value) if it.remaining_value is not None else None,
            "planned_start": date_ts(it.planned_start), "planned_end": date_ts(it.planned_end), "due_date": date_ts(it.due_date),
            "actual_start": day_ts(it.actual_start), "actual_end": day_ts(it.actual_end),
            "assignee": {"id": person_id, "handle": handle, "team": team}, "team": {"slug": team},
            "tags": sorted(tags.get(it.id, [])), "risks": risks.get(it.id, []), "stage": stage,
            "placement": {"entered_at": date_ts(pl.entered_at), "exited_at": date_ts(pl.exited_at), "process": pkey, "stage": st.key if st else None} if pl else blank("Placement"),
            "classification": cls.get(it.id, {}), "is_provisional": it.is_provisional, "created_at": date_ts(it.created_at), "closed_at": day_ts(it.closed_at),
            "_project_id": it.project_id, "_team_id": it.team_id, "_assignee_id": it.assignee_id,
        }  # fmt: skip
    return out


def person_root(member: m.WorkspaceMember, person: m.Person, team: str | None) -> dict[str, Any]:
    """Build the CEL `person` root for a member."""
    return {
        "id": person.id, "handle": member.handle, "team": team,
        "capacity_hours_per_week": float(member.capacity_hours_per_week) if member.capacity_hours_per_week is not None else None,
        "time_zone": person.time_zone,
    }  # fmt: skip


def activity_root(a: m.Activity, item_key: str | None, now: datetime) -> dict[str, Any]:
    """Build the CEL `activity` root, with its duration measured up to now when still open."""
    from datetime import timedelta

    end = a.ended_at or now
    return {
        "id": a.id, "kind": a.kind.value, "status": a.status.value, "started_at": date_ts(a.started_at), "ended_at": date_ts(a.ended_at),
        "duration": end - a.started_at if a.started_at else timedelta(0), "person": a.person_id, "item": item_key,
    }  # fmt: skip


def daily_log_root(d: m.DailyLog) -> dict[str, Any]:
    """Build the CEL `daily_log` root."""
    return {
        "date": date_ts(d.date),
        "status": d.status.value,
        "minutes_total": d.minutes_total,
        "item_ids": list(d.item_ids or []),
    }


def event_root(ev: m.Event | None) -> dict[str, Any]:
    """Build the CEL `event` root; empty values when there is no event."""
    if ev is None:
        return {"type": "", "source": "", "occurred_at": None, "payload": {}}
    return {
        "type": ev.event_type,
        "source": ev.source.value,
        "occurred_at": date_ts(ev.occurred_at),
        "payload": _plain(ev.payload or {}),
    }


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_plain(x) for x in v]
    return v


__all__ = [
    "ActivityStatus",
    "activity_root",
    "blank",
    "blank_context",
    "daily_log_root",
    "event_root",
    "load_items",
    "person_root",
]
