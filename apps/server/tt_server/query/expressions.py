"""Reusable SQL fragments of the item query: the parsed filter, hours, top risk, open violations, last touched, the current stage and the filtered-items base select."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from tt_filter import Node, parse
from tt_schema.enums import TargetType

from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.query.compile import (
    OPEN_VIOLATION,
    QueryCtx,
    compile_filter,
    risk_query,
)


def _parse(raw: Any) -> Node:
    return parse(raw or {})


def _hours() -> Any:
    return (
        select(func.coalesce(func.sum(m.TimeEntry.minutes), 0) / 60.0)
        .where(m.TimeEntry.work_item_id == m.WorkItem.id)
        .correlate(m.WorkItem)
        .scalar_subquery()
    )


def _top_risk() -> Any:
    return (
        risk_query()
        .with_only_columns(func.max(m.Risk.probability * m.Risk.impact))
        .correlate(m.WorkItem)
        .scalar_subquery()
    )


def _open_violations() -> Any:
    return (
        select(func.count())
        .select_from(m.Violation)
        .where(
            m.Violation.target_type == TargetType.WORK_ITEM,
            m.Violation.target_id == m.WorkItem.id,
            m.Violation.status.in_(OPEN_VIOLATION),
        )
        .correlate(m.WorkItem)
        .scalar_subquery()
    )


def _last_touched() -> Any:
    return (
        select(func.max(m.Event.occurred_at))
        .where(m.Event.entity_type == TargetType.WORK_ITEM, m.Event.entity_id == m.WorkItem.id)
        .correlate(m.WorkItem)
        .scalar_subquery()
    )


def current_stage(ids: list[str] | None = None, restrict: Any = None) -> Any:
    """One current placement per item (the newest when an item sits in several processes)."""
    q = (
        select(
            m.StagePlacement.target_id.label("item_id"),
            m.Stage.key.label("stage"),
            m.StagePlacement.entered_at.label("entered_at"),
            m.Stage.expected_duration_days.label("expected_days"),
            m.Process.key.label("process"),
        )
        .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
        .join(m.Process, m.Process.id == m.StagePlacement.process_id)
        .where(m.StagePlacement.target_type == TargetType.WORK_ITEM, m.StagePlacement.exited_at.is_(None))
    )
    if ids is not None:
        q = q.where(m.StagePlacement.target_id.in_(ids))
    if restrict is not None:
        q = q.where(m.StagePlacement.target_id.in_(restrict))
    return (
        q.distinct(m.StagePlacement.target_id)
        .order_by(m.StagePlacement.target_id, m.StagePlacement.entered_at.desc())
        .subquery("cur")
    )


async def filtered_items(qc: QueryCtx, raw_filter: Any, *cols: Any, where: Any = None) -> sa.Select[Any]:
    """`SELECT cols FROM work_items ...` with the filter, workspace boundary and ACL applied."""
    pred = await compile_filter(_parse(raw_filter), qc)
    stmt = (
        select(*cols)
        .select_from(m.WorkItem)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(m.Project.workspace_id == qc.ws_id, m.WorkItem.deleted_at.is_(None), pred)
    )
    if where is not None:
        stmt = stmt.where(where)
    result: sa.Select[Any] = visible(stmt, qc.acl, m.WorkItem)
    return result
