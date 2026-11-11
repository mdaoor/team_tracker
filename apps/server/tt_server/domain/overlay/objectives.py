"""Objectives and key results: listing and progress updates (creation lives in `products.create_objective`)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import NotFound


async def list_objectives(uow: UnitOfWork, ws_id: str) -> list[m.Objective]:
    """Live objectives of the workspace."""
    return list(
        (
            await uow.session.scalars(
                select(m.Objective).where(m.Objective.workspace_id == ws_id, m.Objective.deleted_at.is_(None))
            )
        ).all()
    )


async def add_key_result(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, oid: str, data: dict[str, Any]
) -> m.KeyResult:
    """Attach a key result to an objective of the workspace."""
    o = await uow.session.scalar(
        select(m.Objective).where(m.Objective.id == oid, m.Objective.workspace_id == ws.id)
    )
    if o is None:
        raise NotFound("not_found", "objective not found")
    kr = m.KeyResult(id=new_id(), objective_id=o.id, **data)
    uow.session.add(kr)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.OBJECTIVE,
        entity_id=o.id,
        event_type="key_result.updated",
        payload={"after": {"key_result_id": kr.id, "current": str(kr.current_value)}},
    )
    return kr


async def update_key_result(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, oid: str, kid: str, current_value: Decimal
) -> m.KeyResult:
    """Record new progress on a key result."""
    kr = await uow.session.scalar(
        select(m.KeyResult)
        .join(m.Objective, m.Objective.id == m.KeyResult.objective_id)
        .where(m.KeyResult.id == kid, m.Objective.id == oid, m.Objective.workspace_id == ws.id)
    )
    if kr is None:
        raise NotFound("not_found", "key result not found")
    before = kr.current_value
    kr.current_value = current_value
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.OBJECTIVE,
        entity_id=oid,
        event_type="key_result.updated",
        payload={"before": {"current": str(before)}, "after": {"current": str(current_value)}},
    )
    return kr


async def list_key_results(uow: UnitOfWork, ws_id: str, oid: str) -> list[m.KeyResult]:
    """Key results of one objective of the workspace."""
    return list(
        (
            await uow.session.scalars(
                select(m.KeyResult)
                .join(m.Objective, m.Objective.id == m.KeyResult.objective_id)
                .where(m.Objective.id == oid, m.Objective.workspace_id == ws_id)
            )
        ).all()
    )
