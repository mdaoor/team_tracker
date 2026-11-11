"""What the LLM gateway's tools do on the server: fuzzy item search, editing a review suggestion, storing a suggested classification.

The routers in `api/routers/llm.py` parse, authorise, call these and commit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import TargetType, ValueSource

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import NotFound
from tt_server.domain.overlay import classify as classify_svc


async def search_items(
    session: AsyncSession,
    acl: AclContext,
    ws_id: str,
    me: str,
    *,
    q: str,
    project: str | None,
    assignee: str | None,
    state_category: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Trigram-similarity search over the titles the viewer may see (`assignee` may be `me`); best matches first."""
    score = func.similarity(m.WorkItem.title, q).label("score")
    stmt = (
        select(m.WorkItem.key, m.WorkItem.title, m.Project.key, m.WorkflowState.category, score)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(
            m.Project.workspace_id == ws_id,
            m.WorkItem.deleted_at.is_(None),
            or_(m.WorkItem.title.op("%")(q), m.WorkItem.title.ilike(f"%{q}%")),
        )
    )
    stmt = visible(stmt, acl, m.WorkItem)
    if project:
        stmt = stmt.where(func.lower(m.Project.key) == project.lower())
    if assignee:
        pid = (
            me
            if assignee == "me"
            else await session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.handle == assignee
                )
            )
        )
        stmt = stmt.where(m.WorkItem.assignee_id == pid)
    if state_category:
        stmt = stmt.where(m.WorkflowState.category.in_(state_category.split(",")))
    rows = (await session.execute(stmt.order_by(score.desc(), m.WorkItem.key).limit(limit))).all()
    return [
        {"key": k, "title": t, "project_key": p, "state_category": c.value, "score": round(float(s), 3)}
        for k, t, p, c, s in rows
    ]


async def edit_review_item(
    uow: UnitOfWork,
    ws_id: str,
    actor_id: str,
    rid: str,
    *,
    suggestion: dict[str, Any] | None,
    confidence: float | None,
    status: str | None,
) -> m.ReviewItem:
    """Edit the stored suggestion / confidence of a review row, or ignore (`ignored`) / reopen (`open`) it; emits `review.updated`."""
    row = await uow.session.get(m.ReviewItem, rid)
    if row is None:
        raise NotFound("not_found", "review item not found")
    uow.context["actor_id"] = actor_id
    before = {
        "suggestion": row.suggestion,
        "confidence": float(row.confidence) if row.confidence is not None else None,
    }
    if suggestion is not None:
        row.suggestion = {**(row.suggestion or {}), **suggestion}
    if confidence is not None:
        row.confidence = confidence  # type: ignore[assignment]
    if status is not None:
        row.status = "resolved" if status == "ignored" else "open"
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=row.target_id,
        event_type="review.updated",
        payload={"before": before, "after": {"suggestion": row.suggestion, "status": row.status}},
    )
    return row


async def store_suggested_classification(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor_id: str,
    *,
    dimension: m.Dimension,
    value_ref: str,
    target_id: str,
    confidence: float,
) -> m.Classification | None:
    """Store an *unconfirmed* rule-sourced classification of an item; None when a manual one protects the dimension."""
    value = await uow.session.scalar(
        select(m.DimensionValue).where(
            m.DimensionValue.dimension_id == dimension.id,
            m.DimensionValue.deleted_at.is_(None),
            m.DimensionValue.is_active.is_(True),
            (m.DimensionValue.key == value_ref) | (m.DimensionValue.id == value_ref),
        )
    )
    if value is None:
        raise NotFound("not_found", f"value {value_ref!r} not found")
    return await classify_svc.classify(
        uow,
        ws,
        target_type=TargetType.WORK_ITEM,
        target_id=target_id,
        dimension=dimension,
        value=value,
        source=ValueSource.RULE,
        set_by=actor_id,
        confidence=confidence,
        is_confirmed=False,
    )
