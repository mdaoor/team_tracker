"""Metric snapshots (Phase 8 §8.5): one row per scope x day x weight scheme, over a trailing 30-day window ending that day."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import EventSource, RiskStatus, ScopeType, StateCategory, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.governance.facts import OPEN

WINDOW_DAYS = 30


def _p50(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


async def snapshot_day(uow: UnitOfWork, ws: m.Workspace, day: date, *, scheme_id: str | None = None) -> int:
    """Write (replace) the snapshots of `day` for every scope; returns the number of rows."""
    s: AsyncSession = uow.session
    sysp = await system_person(s)
    start = day - timedelta(days=WINDOW_DAYS - 1)
    end_dt = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
    prod_of: dict[str, str] = {
        pid: prod
        for pid, prod in (
            await s.execute(
                select(m.ProductLink.target_id, m.ProductLink.product_id).where(
                    m.ProductLink.target_type == TargetType.PROJECT, m.ProductLink.deleted_at.is_(None)
                )
            )
        ).all()
    }
    weights: dict[str, float] = {}
    if scheme_id:
        weights = {
            wid: float(v)
            for wid, v in (
                await s.execute(
                    select(m.Weight.work_item_id, m.Weight.value).where(m.Weight.scheme_id == scheme_id)
                )
            ).all()
        }
    rows = (
        await s.execute(
            select(m.WorkItem, m.WorkflowState.category)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(
                m.Project.workspace_id == ws.id,
                m.WorkItem.deleted_at.is_(None),
                m.WorkItem.is_template.is_(False),
                m.WorkItem.created_at < end_dt,
            )
        )
    ).all()
    hours: dict[str, float] = defaultdict(float)
    for wid, mins in (
        await s.execute(
            select(m.TimeEntry.work_item_id, func.sum(m.TimeEntry.minutes))
            .where(m.TimeEntry.date >= start, m.TimeEntry.date <= day, m.TimeEntry.deleted_at.is_(None))
            .group_by(m.TimeEntry.work_item_id)
        )
    ).all():
        hours[wid] = float(mins or 0) / 60
    placements = (
        await s.execute(
            select(
                m.StagePlacement.target_id,
                m.Stage.key,
                m.StagePlacement.entered_at,
                m.StagePlacement.exited_at,
            )
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .where(m.StagePlacement.target_type == TargetType.WORK_ITEM)
        )
    ).all()
    cur_stage: dict[str, str] = {}
    cycle: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))  # item -> stage -> days
    for tid, sk, ent, ext in placements:
        if ext is None:
            cur_stage[tid] = sk
        if ent < end_dt and (ext or end_dt) >= datetime(start.year, start.month, start.day, tzinfo=UTC):
            cycle[tid][sk].append(((ext or end_dt) - ent).total_seconds() / 86400)
    risk_by_item: dict[str, int] = defaultdict(int)
    for wid, p, i in (
        await s.execute(
            select(m.RiskLink.work_item_id, m.Risk.probability, m.Risk.impact)
            .join(m.Risk, m.Risk.id == m.RiskLink.risk_id)
            .where(
                m.RiskLink.deleted_at.is_(None),
                m.Risk.status != RiskStatus.CLOSED,
                m.Risk.deleted_at.is_(None),
            )
        )
    ).all():
        risk_by_item[wid] += p * i
    viol = (
        await s.execute(
            select(
                m.Violation.person_id,
                m.Violation.team_id,
                m.Violation.project_id,
                m.Violation.product_id,
                m.Violation.category,
            ).where(
                m.Violation.workspace_id == ws.id,
                m.Violation.status.in_(OPEN),
                m.Violation.detected_at < end_dt,
            )
        )
    ).all()
    habit_scores: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for st, sid, key, score in (
        await s.execute(
            select(m.Habit.subject_type, m.Habit.subject_id, m.Habit.key, m.Habit.score).where(
                m.Habit.workspace_id == ws.id, m.Habit.deleted_at.is_(None), m.Habit.window == "30d"
            )
        )
    ).all():
        habit_scores[(st.value, sid)][key] = float(score)

    scopes: dict[tuple[ScopeType, str], list[tuple[m.WorkItem, StateCategory]]] = defaultdict(list)
    for it, cat in rows:
        w = weights.get(it.id, float(it.weight or 0)) if scheme_id else float(it.weight or 0)
        it.__dict__["_w"] = w
        keys = [(ScopeType.WORKSPACE, ws.id), (ScopeType.PROJECT, it.project_id)]
        if it.assignee_id:
            keys.append((ScopeType.PERSON, it.assignee_id))
        if it.team_id:
            keys.append((ScopeType.TEAM, it.team_id))
        if (pr := prod_of.get(it.project_id)) is not None:
            keys.append((ScopeType.PRODUCT, pr))
        if (stg := cur_stage.get(it.id)) is not None:
            keys.append(
                (ScopeType.PROCESS, f"{ws.id}:{stg}")
            )  # the `stage` scope: stored as scope_type=process, scope_id=`<workspace id>:<stage key>`
        for k in keys:
            scopes[k].append((it, cat))
    out = 0
    scheme_cond = (
        m.MetricSnapshot.weight_scheme_id.is_(None)
        if scheme_id is None
        else m.MetricSnapshot.weight_scheme_id == scheme_id
    )
    scope_ids = sorted({k[1] for k in scopes})
    if scope_ids:
        await s.execute(
            delete(m.MetricSnapshot).where(
                m.MetricSnapshot.date == day, scheme_cond, m.MetricSnapshot.scope_id.in_(scope_ids)
            )
        )
    for (scope_type, sid), members in scopes.items():
        done = [
            it
            for it, c in members
            if c == StateCategory.DONE and it.actual_end and start <= it.actual_end.date() <= day
        ]
        open_ = [it for it, c in members if c in (StateCategory.OPEN, StateCategory.ACTIVE)]
        active = [it for it, c in members if c == StateCategory.ACTIVE]
        late = [
            it for it in done if it.planned_end and it.actual_end and it.actual_end.date() > it.planned_end
        ]
        est_pairs = [
            (float(it.estimate_value) * (8 if it.estimate_unit.value == "days" else 1), hours.get(it.id, 0.0))
            for it in done
            if it.estimate_value and it.estimate_unit.value in ("hours", "days") and hours.get(it.id, 0) > 0
        ]
        stage_cycle: dict[str, list[float]] = defaultdict(list)
        for it, _ in members:
            for sk, ds in cycle.get(it.id, {}).items():
                stage_cycle[sk] += ds
        lead = [
            (
                datetime(it.actual_end.year, it.actual_end.month, it.actual_end.day, tzinfo=UTC)  # type: ignore[union-attr]
                - it.created_at
            ).total_seconds()
            / 86400
            for it in done
        ]
        id_set = {it.id for it, _ in members}
        vcount: dict[str, int] = defaultdict(int)
        for vperson, vteam, prj, prd, vcat in viol:
            hit = {
                ScopeType.PERSON: vperson,
                ScopeType.TEAM: vteam,
                ScopeType.PROJECT: prj,
                ScopeType.PRODUCT: prd,
                ScopeType.WORKSPACE: ws.id,
            }.get(scope_type)
            if hit == sid:
                vcount[vcat.value] += 1
        metrics: dict[str, Any] = {
            "window_days": WINDOW_DAYS,
            "throughput_weighted": round(sum(it.__dict__["_w"] for it in done), 4),
            "value_delivered": round(sum(float(it.value_score or 0) for it in done), 4),
            "hours_logged": round(sum(hours.get(i, 0.0) for i in id_set), 2),
            "cycle_time_p50": {k: _p50(v) for k, v in sorted(stage_cycle.items())},
            "lead_time_p50": _p50(lead),
            "wip": len(active),
            "open_items": len(open_),
            "blocked_count": 0,
            "overrun_rate": round(len(late) / len(done), 4) if done else None,
            "estimate_accuracy": round(1 - statistics.mean(abs(e - a) / e for e, a in est_pairs), 4) if est_pairs else None,
            "violation_count": dict(vcount),
            "habit_scores": habit_scores.get((scope_type.value, sid), {}),
            "risk_exposure": sum(risk_by_item.get(it.id, 0) for it in open_),
            "inputs": {"item_ids_done": [it.id for it in done][:500]},
        }  # fmt: skip
        s.add(
            m.MetricSnapshot(
                id=new_id(),
                scope_type=scope_type,
                scope_id=sid,
                date=day,
                weight_scheme_id=scheme_id,
                metrics=metrics,
            )
        )
        out += 1
    await s.flush()
    emit(
        uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.PERSON, entity_id=sysp.id, event_type="metrics.snapshot_written",
        payload={"after": {"date": day.isoformat(), "scheme": scheme_id, "rows": out}}, source=EventSource.RULE,
    )  # fmt: skip
    return out


async def update_key_results(uow: UnitOfWork, ws: m.Workspace) -> int:
    """`KeyResult{source=metric_snapshot, metric_key="throughput_weighted"|"violation_count.process"|...}` <- the scope's latest snapshot."""
    s = uow.session
    sysp = await system_person(s)
    n = 0
    krs = (
        await s.execute(
            select(m.KeyResult, m.Objective)
            .join(m.Objective, m.Objective.id == m.KeyResult.objective_id)
            .where(
                m.Objective.workspace_id == ws.id,
                m.KeyResult.source == "metric_snapshot",
                m.KeyResult.metric_key.is_not(None),
                m.KeyResult.deleted_at.is_(None),
            )
        )
    ).all()
    for kr, obj in krs:
        snap = await s.scalar(
            select(m.MetricSnapshot)
            .where(
                m.MetricSnapshot.scope_type == obj.scope_type,
                m.MetricSnapshot.scope_id
                == (ws.id if obj.scope_type == ScopeType.WORKSPACE else obj.scope_id),
                m.MetricSnapshot.weight_scheme_id.is_(None),
            )
            .order_by(m.MetricSnapshot.date.desc())
        )
        if snap is None:
            continue
        val: Any = snap.metrics
        for part in (kr.metric_key or "").split("."):
            val = val.get(part) if isinstance(val, dict) else None
        if isinstance(val, dict):
            val = sum(v for v in val.values() if isinstance(v, int | float))
        if isinstance(val, int | float) and float(kr.current_value) != float(val):
            before = float(kr.current_value)
            kr.current_value = type(kr.current_value)(str(val))
            emit(
                uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.OBJECTIVE, entity_id=obj.id, event_type="key_result.updated",
                payload={"before": {"current_value": before}, "after": {"key_result_id": kr.id, "current_value": float(val), "metric": kr.metric_key}}, source=EventSource.RULE,
            )  # fmt: skip
            n += 1
    return n
