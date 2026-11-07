"""Dimensions, their values and manual classification (Phase 3): the services behind `/dimensions` and `/classifications`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import aliased
from tt_schema.base import new_id
from tt_schema.enums import DimensionKind, TargetType, ValueSource, ValueTypeShape

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import item_visible_for, visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit_admin
from tt_server.domain.errors import Conflict, Invalid, NotFound
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import mirrors, weights


async def list_dimensions(uow: UnitOfWork, ws_id: str) -> list[m.Dimension]:
    """All live dimensions of the workspace, seeding the builtin ones first."""
    await mirrors.ensure_builtin_dimensions(uow.session, ws_id)
    return list(
        (
            await uow.session.scalars(
                select(m.Dimension)
                .where(m.Dimension.workspace_id == ws_id, m.Dimension.deleted_at.is_(None))
                .order_by(m.Dimension.key)
            )
        ).all()
    )


async def get_dimension(uow: UnitOfWork, ws_id: str, ref: str) -> m.Dimension:
    """Resolve a dimension by id or key; 404 when absent."""
    d = await uow.session.scalar(
        select(m.Dimension).where(
            m.Dimension.workspace_id == ws_id,
            m.Dimension.deleted_at.is_(None),
            (m.Dimension.id == ref) | (m.Dimension.key == ref),
        )
    )
    if d is None:
        raise NotFound("not_found", "dimension not found")
    return d


async def create_dimension(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any], applies_to: list[TargetType]
) -> m.Dimension:
    """Create a custom dimension; keys are immutable and unique per workspace."""
    if await uow.session.scalar(
        select(m.Dimension.id).where(m.Dimension.workspace_id == ws.id, m.Dimension.key == data["key"])
    ):
        raise Conflict(
            "dimension_exists", f"dimension {data['key']!r} exists (keys are immutable)", pointer="/key"
        )
    d = m.Dimension(id=new_id(), workspace_id=ws.id, kind=DimensionKind.CUSTOM, **data)
    d.applies_to = [t.value for t in applies_to]
    uow.session.add(d)
    await uow.session.flush()
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="dimension.created",
        payload={"after": {"key": d.key}},
    )
    return d


def _require_custom(d: m.Dimension) -> None:
    if d.kind == DimensionKind.BUILTIN:
        raise Conflict("builtin_dimension", "values of builtin dimensions follow their backing rows")


async def list_values(uow: UnitOfWork, d: m.Dimension) -> list[m.DimensionValue]:
    """Live values of a dimension in display order."""
    return list(
        (
            await uow.session.scalars(
                select(m.DimensionValue)
                .where(m.DimensionValue.dimension_id == d.id, m.DimensionValue.deleted_at.is_(None))
                .order_by(m.DimensionValue.position, m.DimensionValue.key)
            )
        ).all()
    )


async def create_value(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, d: m.Dimension, data: dict[str, Any]
) -> m.DimensionValue:
    """Add one value to a custom dimension (tree parents only on tree dimensions)."""
    _require_custom(d)
    if await uow.session.scalar(
        select(m.DimensionValue.id).where(
            m.DimensionValue.dimension_id == d.id,
            m.DimensionValue.key == data["key"],
            m.DimensionValue.deleted_at.is_(None),
        )
    ):
        raise Conflict("value_exists", f"value {data['key']!r} exists", pointer="/key")
    if data.get("parent_value_id") and d.value_type != ValueTypeShape.TREE:
        raise Invalid("not_a_tree", "this dimension is flat", pointer="/parent_value_id")
    v = m.DimensionValue(id=new_id(), dimension_id=d.id, **data)
    uow.session.add(v)
    await uow.session.flush()
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="dimension_value.created",
        payload={"after": {"dimension": d.key, "key": v.key}},
    )
    return v


async def bulk_values(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, d: m.Dimension, names: list[str]
) -> int:
    """Create a value per new name (slugified key); returns how many were created."""
    _require_custom(d)
    have = set(
        (
            await uow.session.scalars(
                select(m.DimensionValue.key).where(m.DimensionValue.dimension_id == d.id)
            )
        ).all()
    )
    created = 0
    for pos, name in enumerate(names):
        key = name.strip().lower().replace(" ", "-")
        if key and key not in have:
            uow.session.add(
                m.DimensionValue(id=new_id(), dimension_id=d.id, key=key, name=name.strip(), position=pos)
            )
            have.add(key)
            created += 1
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="dimension_value.created",
        payload={"after": {"dimension": d.key, "count": created}},
    )
    return created


async def would_cycle(uow: UnitOfWork, value_id: str, parent_id: str | None) -> bool:
    """True when making `parent_id` the parent of `value_id` would loop the tree."""
    seen = {value_id}
    while parent_id:
        if parent_id in seen:
            return True
        seen.add(parent_id)
        parent_id = await uow.session.scalar(
            select(m.DimensionValue.parent_value_id).where(m.DimensionValue.id == parent_id)
        )
    return False


async def patch_value(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, d: m.Dimension, value_id: str, data: dict[str, Any]
) -> m.DimensionValue:
    """Edit a value (name, parent, active flag, position); a parent change may not create a cycle."""
    v = await uow.session.scalar(
        select(m.DimensionValue).where(m.DimensionValue.id == value_id, m.DimensionValue.dimension_id == d.id)
    )
    if v is None:
        raise NotFound("not_found", "value not found")
    if "parent_value_id" in data and await would_cycle(uow, v.id, data["parent_value_id"]):
        raise Invalid("cycle", "a value cannot be its own ancestor", pointer="/parent_value_id")
    for k, val in data.items():
        setattr(v, k, val)
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="dimension_value.updated",
        payload={"fields_changed": list(data)},
    )
    return v


async def find_value(uow: UnitOfWork, d: m.Dimension, ref: str) -> m.DimensionValue:
    """An active value of `d` by key or id; 404 (pointing at /value) when absent."""
    value = await uow.session.scalar(
        select(m.DimensionValue).where(
            m.DimensionValue.dimension_id == d.id,
            m.DimensionValue.deleted_at.is_(None),
            m.DimensionValue.is_active.is_(True),
            (m.DimensionValue.key == ref) | (m.DimensionValue.id == ref),
        )
    )
    if value is None:
        raise NotFound("not_found", f"value {ref!r} not found in {d.key}", pointer="/value")
    return value


async def classify_manually(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor_id: str,
    *,
    target_type: TargetType,
    target_id: str,
    dimension: m.Dimension,
    value: m.DimensionValue,
) -> m.Classification | None:
    """Record a manual classification and refresh the weight of a classified work item."""
    row = await classify_svc.classify(
        uow,
        ws,
        target_type=target_type,
        target_id=target_id,
        dimension=dimension,
        value=value,
        source=ValueSource.MANUAL,
        set_by=actor_id,
    )
    item = await uow.session.get(m.WorkItem, target_id) if target_type == TargetType.WORK_ITEM else None
    if item is not None:
        await weights.recompute_item(uow, ws, item, actor_id=actor_id)
    return row


async def classification_rows(
    uow: UnitOfWork,
    ws_id: str,
    acl: AclContext,
    target_type: TargetType,
    *,
    target_id: str | None,
    dimension: str | None,
    confirmed: bool | None,
) -> list[tuple[m.Classification, str, str]]:
    """Classifications of targets the caller can see, as (row, dimension key, value key); at most 500."""
    stmt = (
        select(m.Classification, m.Dimension.key, m.DimensionValue.key)
        .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
        .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
        .where(
            m.Classification.workspace_id == ws_id,
            m.Classification.target_type == target_type,
            m.Classification.deleted_at.is_(None),
        )
    )
    if target_type == TargetType.WORK_ITEM:  # only classifications of items the actor can see
        wi = aliased(m.WorkItem)
        stmt = stmt.where(m.Classification.target_id.in_(select(wi.id).where(item_visible_for(acl, wi))))
    elif target_type == TargetType.PROJECT:
        stmt = stmt.where(
            m.Classification.target_id.in_(
                visible(select(m.Project.id).where(m.Project.workspace_id == ws_id), acl, m.Project)
            )
        )
    elif target_type == TargetType.PRODUCT:
        stmt = stmt.where(
            m.Classification.target_id.in_(
                visible(select(m.Product.id).where(m.Product.workspace_id == ws_id), acl, m.Product)
            )
        )
    if target_id:
        stmt = stmt.where(m.Classification.target_id == target_id)
    if dimension:
        stmt = stmt.where(m.Dimension.key == dimension)
    if confirmed is not None:
        stmt = stmt.where(m.Classification.is_confirmed.is_(confirmed))
    return [(c, dk, vk) for c, dk, vk in (await uow.session.execute(stmt.limit(500))).all()]


async def get_classification(
    uow: UnitOfWork, ws_id: str, cid: str, *, live_only: bool = False
) -> m.Classification:
    """A classification of the workspace by id (404 when absent)."""
    stmt = select(m.Classification).where(m.Classification.id == cid, m.Classification.workspace_id == ws_id)
    if live_only:
        stmt = stmt.where(m.Classification.deleted_at.is_(None))
    c = await uow.session.scalar(stmt)
    if c is None:
        raise NotFound("not_found", "classification not found")
    return c
