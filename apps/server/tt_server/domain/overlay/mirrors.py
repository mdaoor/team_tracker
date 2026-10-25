"""Builtin dimensions and the mirrors that keep them in sync with real columns (architecture §4.3).

Every builtin FK of a WorkItem (project, team, iteration, milestone, release, component, type, priority)
is mirrored into a `Classification{source=rule, is_confirmed=true}` so all dimensions are queried one way.
Mirrors are plain domain hooks: they write rows but emit no events of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import (
    Cardinality,
    DimensionKind,
    Priority,
    TargetType,
    ValueSource,
    ValueTypeShape,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.errors import NotFound
from tt_server.domain.overlay.classify import classify, remove_rule_classification

W, P, PR, PE = TargetType.WORK_ITEM, TargetType.PROJECT, TargetType.PRODUCT, TargetType.PERSON


@dataclass(frozen=True)
class BuiltinDim:
    key: str
    name: str
    shape: ValueTypeShape
    cardinality: Cardinality
    applies_to: tuple[TargetType, ...]


BUILTIN_DIMENSIONS = [
    BuiltinDim("project", "Project", ValueTypeShape.TREE, Cardinality.SINGLE, (W,)),
    BuiltinDim("product", "Product", ValueTypeShape.TREE, Cardinality.SINGLE, (W, P)),
    BuiltinDim("stage", "Stage", ValueTypeShape.FLAT, Cardinality.SINGLE, (W, P, PR)),
    BuiltinDim("team", "Team", ValueTypeShape.TREE, Cardinality.SINGLE, (W, P, PE)),
    BuiltinDim("objective", "Objective", ValueTypeShape.FLAT, Cardinality.MULTI, (W, P, PR)),
    BuiltinDim("priority", "Priority", ValueTypeShape.FLAT, Cardinality.SINGLE, (W, P, PR)),
    BuiltinDim("type", "Type", ValueTypeShape.FLAT, Cardinality.SINGLE, (W,)),
    BuiltinDim("iteration", "Iteration", ValueTypeShape.FLAT, Cardinality.SINGLE, (W,)),
    BuiltinDim("milestone", "Milestone", ValueTypeShape.FLAT, Cardinality.SINGLE, (W,)),
    BuiltinDim("release", "Release", ValueTypeShape.FLAT, Cardinality.SINGLE, (W,)),
    BuiltinDim("component", "Component", ValueTypeShape.FLAT, Cardinality.SINGLE, (W,)),
]


async def ensure_builtin_dimensions(session: AsyncSession, ws_id: str) -> dict[str, m.Dimension]:
    """Idempotently create the builtin dimensions (and the fixed priority values) of a workspace."""
    existing = {
        d.key: d
        for d in (
            await session.scalars(
                select(m.Dimension).where(m.Dimension.workspace_id == ws_id, m.Dimension.deleted_at.is_(None))
            )
        ).all()
    }
    for spec in BUILTIN_DIMENSIONS:
        if spec.key in existing:
            continue
        dim = m.Dimension(
            id=new_id(),
            workspace_id=ws_id,
            key=spec.key,
            name=spec.name,
            kind=DimensionKind.BUILTIN,
            value_type=spec.shape,
            cardinality=spec.cardinality,
            applies_to=[t.value for t in spec.applies_to],
        )
        session.add(dim)
        existing[spec.key] = dim
    await session.flush()
    prio = existing["priority"]
    have = set(
        (
            await session.scalars(
                select(m.DimensionValue.key).where(m.DimensionValue.dimension_id == prio.id)
            )
        ).all()
    )
    for pos, p in enumerate(Priority):
        if p.value not in have:
            session.add(
                m.DimensionValue(
                    id=new_id(), dimension_id=prio.id, key=p.value, name=p.value.title(), position=pos
                )
            )
    await session.flush()
    return existing


async def get_dimension(session: AsyncSession, ws_id: str, key: str) -> m.Dimension:
    """A live dimension of the workspace by key; 404 when absent."""
    dim = await session.scalar(
        select(m.Dimension).where(
            m.Dimension.workspace_id == ws_id, m.Dimension.key == key, m.Dimension.deleted_at.is_(None)
        )
    )
    if dim is None:
        dims = await ensure_builtin_dimensions(session, ws_id)
        if key in dims:
            return dims[key]

        raise NotFound("not_found", f"dimension {key!r} not found")
    return dim


async def ensure_value(
    session: AsyncSession,
    dim: m.Dimension,
    *,
    backing_type: str | None,
    backing_id: str | None,
    key: str,
    name: str,
    parent_backing_id: str | None = None,
) -> m.DimensionValue:
    """Find or create the value backed by a real row; keeps key/name/parent in sync."""
    stmt = select(m.DimensionValue).where(m.DimensionValue.dimension_id == dim.id)
    stmt = (
        stmt.where(m.DimensionValue.backing_id == backing_id)
        if backing_id
        else stmt.where(m.DimensionValue.key == key)
    )
    value = await session.scalar(stmt)
    parent_id = None
    if parent_backing_id:
        parent_id = await session.scalar(
            select(m.DimensionValue.id).where(
                m.DimensionValue.dimension_id == dim.id, m.DimensionValue.backing_id == parent_backing_id
            )
        )
    # `backing_type` is a TargetType in the spec; stage/iteration/... have none, `backing_id` identifies them
    valid_types = {t.value for t in TargetType}
    backing = TargetType(backing_type) if backing_type in valid_types else None
    if value is None:
        value = m.DimensionValue(
            id=new_id(),
            dimension_id=dim.id,
            key=key,
            name=name,
            backing_type=backing,
            backing_id=backing_id,
            parent_value_id=parent_id,
        )
        session.add(value)
        await session.flush()
    else:
        value.key, value.name, value.is_active = key, name, True
        if parent_id and value.parent_value_id != parent_id:
            value.parent_value_id = parent_id
        if value.deleted_at is not None:
            value.deleted_at = None
    return value


async def sync_item_mirrors(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, *, set_by: str | None = None
) -> None:
    """Upsert the rule-sourced classifications for the item's real columns (no events)."""

    s = uow.session
    set_by = set_by or uow.context.get("actor_id")
    dims = await ensure_builtin_dimensions(s, ws.id)
    project = await s.get(m.Project, item.project_id)
    team = await s.get(m.Team, item.team_id) if item.team_id else None
    itype = await s.get(m.WorkItemType, item.type_id)
    pairs: list[tuple[str, Any, str, str, str | None]] = []
    if project:
        pairs.append(("project", project, project.key, project.name, project.parent_project_id))
    pairs.append(
        (
            "team",
            team,
            team.slug if team else "",
            team.name if team else "",
            team.parent_team_id if team else None,
        )
    )
    if itype:
        pairs.append(("type", itype, itype.key.value, itype.name, None))
    for dim_key, model, fk in (
        ("iteration", m.Iteration, item.iteration_id),
        ("milestone", m.Milestone, item.milestone_id),
        ("release", m.Release, item.release_id),
        ("component", m.Component, item.component_id),
    ):
        row: Any = await s.get(model, fk) if fk else None
        pairs.append(
            (dim_key, row, row.id[-12:] if row else "", row.name if row else "", None)
        )  # uuid7 prefixes collide within a minute; the tail is random
    for dim_key, row, key, name, parent_id in pairs:
        dim = dims[dim_key]
        if row is None:
            await remove_rule_classification(uow, ws, W, item.id, dim)
            continue
        value = await ensure_value(
            s, dim, backing_type=dim_key, backing_id=row.id, key=key, name=name, parent_backing_id=parent_id
        )
        await classify(
            uow,
            ws,
            target_type=W,
            target_id=item.id,
            dimension=dim,
            value=value,
            source=ValueSource.RULE,
            set_by=set_by,
            emit_event=False,
        )
    prio_value = await s.scalar(
        select(m.DimensionValue).where(
            m.DimensionValue.dimension_id == dims["priority"].id, m.DimensionValue.key == item.priority.value
        )
    )
    if prio_value is not None:
        await classify(
            uow,
            ws,
            target_type=W,
            target_id=item.id,
            dimension=dims["priority"],
            value=prio_value,
            source=ValueSource.RULE,
            set_by=set_by,
            emit_event=False,
        )
