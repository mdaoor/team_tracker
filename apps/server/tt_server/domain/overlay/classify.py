"""Classification service: the glue rows managers write (object model §4)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import Cardinality, EventSource, TargetType, ValueSource

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid


async def _active(
    uow: UnitOfWork, target_type: TargetType, target_id: str, dimension_id: str
) -> list[m.Classification]:
    return list(
        (
            await uow.session.scalars(
                select(m.Classification).where(
                    m.Classification.target_type == target_type,
                    m.Classification.target_id == target_id,
                    m.Classification.dimension_id == dimension_id,
                    m.Classification.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def classify(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    target_type: TargetType,
    target_id: str,
    dimension: m.Dimension,
    value: m.DimensionValue,
    source: ValueSource,
    set_by: str | None,
    confidence: float | None = None,
    is_confirmed: bool = True,
    inherited_from: tuple[TargetType, str] | None = None,
    emit_event: bool = True,
    event_source: EventSource = EventSource.OVERLAY,
    correlation_id: str | None = None,
) -> m.Classification | None:
    """Single cardinality replaces the effective row; a manual row is never overwritten by inherited values.

    Returns the effective row, or None when a manual classification protected the dimension. Idempotent.
    """
    applies = [TargetType(t) for t in (dimension.applies_to or [])]
    if applies and target_type not in applies:
        raise Invalid("not_applicable", f"dimension {dimension.key!r} does not apply to {target_type.value}")
    if value.dimension_id != dimension.id:
        raise Invalid("wrong_dimension", "value belongs to another dimension", pointer="/value")
    existing = await _active(uow, target_type, target_id, dimension.id)
    actor = set_by or uow.context.get("actor_id")
    same = next((c for c in existing if c.value_id == value.id), None)
    previous: dict[str, object] | None = None
    if dimension.cardinality == Cardinality.SINGLE:
        others = [c for c in existing if c.value_id != value.id]
        if others and any(c.source == ValueSource.MANUAL for c in others) and source != ValueSource.MANUAL:
            return None  # propagation / rules never overwrite a manual choice
        if same is not None:
            changed = False
            if source == ValueSource.MANUAL and same.source != ValueSource.MANUAL:
                same.source, same.set_by, same.set_at, changed = ValueSource.MANUAL, set_by, utcnow(), True
            if is_confirmed and not same.is_confirmed:
                same.is_confirmed, same.confirmed_by, changed = True, actor, True
            if changed and emit_event and actor:
                _emit(
                    uow,
                    ws,
                    actor,
                    target_type,
                    target_id,
                    dimension,
                    value,
                    same,
                    "classification.changed",
                    event_source,
                    correlation_id,
                    {"source": source.value},
                )
            return same
        for old in others:
            old.deleted_at = utcnow()
            previous = {"value_id": old.value_id, "source": old.source.value}
        await uow.session.flush()
    elif same is not None:
        return same
    row = m.Classification(
        id=new_id(),
        workspace_id=ws.id,
        target_type=target_type,
        target_id=target_id,
        dimension_id=dimension.id,
        value_id=value.id,
        source=source,
        inherited_from_type=inherited_from[0] if inherited_from else None,
        inherited_from_id=inherited_from[1] if inherited_from else None,
        set_by=set_by,
        set_at=utcnow(),
        confidence=confidence,
        is_confirmed=is_confirmed,
    )
    uow.session.add(row)
    await uow.session.flush()
    if emit_event and actor:
        _emit(
            uow,
            ws,
            actor,
            target_type,
            target_id,
            dimension,
            value,
            row,
            "classification.changed",
            event_source,
            correlation_id,
            {"previous": previous, "source": source.value},
        )
    return row


def _emit(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor: str,
    target_type: TargetType,
    target_id: str,
    dimension: m.Dimension,
    value: m.DimensionValue,
    row: m.Classification,
    event_type: str,
    event_source: EventSource,
    correlation_id: str | None,
    extra: dict[str, object],
) -> None:
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor,
        entity_type=target_type,
        entity_id=target_id,
        event_type=event_type,
        payload={
            "after": {"dimension": dimension.key, "value": value.key, "confirmed": row.is_confirmed},
            **extra,
        },
        source=event_source,
        correlation_id=correlation_id or uow.context.get("correlation_id"),
    )


async def remove_rule_classification(
    uow: UnitOfWork, ws: m.Workspace, target_type: TargetType, target_id: str, dimension: m.Dimension
) -> None:
    """Soft-delete the active rule-sourced classifications of a dimension on a target (manual ones stay)."""
    for c in await _active(uow, target_type, target_id, dimension.id):
        if c.source == ValueSource.RULE:
            c.deleted_at = utcnow()


async def unclassify(uow: UnitOfWork, ws: m.Workspace, cls: m.Classification, *, by: str) -> None:
    """Soft-delete a classification and record the removal."""
    dim = await uow.session.get(m.Dimension, cls.dimension_id)
    value = await uow.session.get(m.DimensionValue, cls.value_id)
    cls.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=by,
        entity_type=cls.target_type,
        entity_id=cls.target_id,
        event_type="classification.changed",
        payload={
            "before": {"dimension": dim.key if dim else None, "value": value.key if value else None},
            "after": None,
        },
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )


async def confirm(uow: UnitOfWork, ws: m.Workspace, cls: m.Classification, *, by: str) -> m.Classification:
    """Confirm a suggestion (the route allows managers and the assignee's line manager)."""
    if not cls.is_confirmed:
        cls.is_confirmed, cls.confirmed_by = True, by
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=by,
            entity_type=cls.target_type,
            entity_id=cls.target_id,
            event_type="classification.confirmed",
            payload={"after": {"classification_id": cls.id}},
            source=EventSource.OVERLAY,
            correlation_id=uow.context.get("correlation_id"),
        )
    return cls


def classified_as(
    dimension_key: str, value_keys: Sequence[str], target_type: TargetType = TargetType.WORK_ITEM
) -> Select[Any]:
    """Subquery of target ids having any of the values in the dimension (the P6 filter compiler uses it)."""
    return (
        select(m.Classification.target_id)
        .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
        .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
        .where(
            m.Dimension.key == dimension_key,
            m.DimensionValue.key.in_(list(value_keys)),
            m.Classification.target_type == target_type,
            m.Classification.deleted_at.is_(None),
        )
    )
