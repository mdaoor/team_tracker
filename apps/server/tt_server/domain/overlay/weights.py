"""Weight schemes, per-scheme Weight rows, overrides and value_score (object model §6, ADR-0007)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EstimationUnit, EventSource, ScopeType, TargetType, ValueSource

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, emit_admin
from tt_server.domain.errors import Invalid, NotFound

DEFAULT_FORMULA: dict[str, Any] = {
    "base": "constant",
    "multipliers": {"priority.high": 2, "priority.highest": 2.5, "priority.critical": 3},
}
HOURS_PER_DAY = Decimal(8)
Q = Decimal("0.0001")


async def ensure_default_scheme(session: AsyncSession, ws_id: str) -> m.WeightScheme:
    """The workspace's `default` weight scheme, created on first use."""
    scheme = await session.scalar(
        select(m.WeightScheme).where(
            m.WeightScheme.workspace_id == ws_id,
            m.WeightScheme.scope_type == ScopeType.WORKSPACE,
            m.WeightScheme.name == "default",
            m.WeightScheme.deleted_at.is_(None),
        )
    )
    if scheme is None:
        scheme = m.WeightScheme(
            id=new_id(),
            workspace_id=ws_id,
            name="default",
            scope_type=ScopeType.WORKSPACE,
            formula=DEFAULT_FORMULA,
        )
        session.add(scheme)
        await session.flush()
    return scheme


def validate_formula(formula: dict[str, Any]) -> None:
    """Reject a scheme formula the evaluator cannot run."""
    base = formula.get("base", "constant")
    if base not in ("estimate_hours", "points", "constant"):
        raise Invalid("invalid_formula", f"unknown base {base!r}", pointer="/formula/base")
    for key, value in (formula.get("multipliers") or {}).items():
        head = key.split(".", 1)[0]
        if head not in ("priority", "stage", "tag", "type", "impact", "dimension") or "." not in key:
            raise Invalid("invalid_formula", f"unknown multiplier {key!r}", pointer="/formula/multipliers")
        try:
            Decimal(str(value))
        except Exception as exc:
            raise Invalid(
                "invalid_formula", f"multiplier {key!r} is not a number", pointer="/formula/multipliers"
            ) from exc
    clamp = formula.get("clamp")
    if clamp is not None and (len(clamp) != 2 or Decimal(str(clamp[0])) > Decimal(str(clamp[1]))):
        raise Invalid("invalid_formula", "clamp must be [min, max]", pointer="/formula/clamp")


def evaluate(formula: dict[str, Any], ctx: dict[str, Any]) -> Decimal:
    """base x product(applicable multipliers), clamped; a missing estimate gives base 1.0."""
    base_kind = formula.get("base", "constant")
    if base_kind == "estimate_hours":
        value = ctx.get("estimate_hours")
    elif base_kind == "points":
        value = ctx.get("points")
    else:
        value = Decimal(str(formula.get("constant", 1.0)))
    weight = Decimal(str(value)) if value is not None and Decimal(str(value)) > 0 else Decimal(1)
    present = {f"priority.{ctx['priority']}", f"type.{ctx['type']}"}
    if ctx.get("stage"):
        present.add(f"stage.{ctx['stage']}")
    if ctx.get("impact") is not None:
        present.add(f"impact.{ctx['impact']}")
    present |= {f"tag.{t}" for t in ctx.get("tags", [])}
    present |= {f"dimension.{d}.{v}" for d, vs in ctx.get("dimensions", {}).items() for v in vs}
    for key, mult in (formula.get("multipliers") or {}).items():
        if key in present:
            weight *= Decimal(str(mult))
    clamp = formula.get("clamp")
    if clamp:
        weight = min(max(weight, Decimal(str(clamp[0]))), Decimal(str(clamp[1])))
    return weight.quantize(Q, rounding=ROUND_HALF_UP)


def estimate_hours(item: m.WorkItem) -> Decimal | None:
    """The item's estimate in hours (days convert at 8 h); points are not converted."""
    if item.estimate_value is None:
        return None
    if item.estimate_unit == EstimationUnit.HOURS:
        return Decimal(item.estimate_value)
    if item.estimate_unit == EstimationUnit.DAYS:
        return Decimal(item.estimate_value) * HOURS_PER_DAY
    return None  # points are not converted (appendix C #19)


async def item_context(session: AsyncSession, item: m.WorkItem) -> dict[str, Any]:
    """The facts a weight formula may reference for an item."""
    itype = await session.get(m.WorkItemType, item.type_id)
    tags = (
        await session.scalars(
            select(m.Tag.name)
            .join(m.TagLink, m.TagLink.tag_id == m.Tag.id)
            .where(
                m.TagLink.target_type == TargetType.WORK_ITEM,
                m.TagLink.target_id == item.id,
                m.TagLink.deleted_at.is_(None),
            )
        )
    ).all()
    rows = (
        await session.execute(
            select(m.Dimension.key, m.DimensionValue.key)
            .join(m.Classification, m.Classification.dimension_id == m.Dimension.id)
            .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
            .where(
                m.Classification.target_type == TargetType.WORK_ITEM,
                m.Classification.target_id == item.id,
                m.Classification.deleted_at.is_(None),
            )
        )
    ).all()
    dims: dict[str, list[str]] = {}
    for dk, vk in rows:
        dims.setdefault(dk, []).append(vk)
    return {
        "estimate_hours": estimate_hours(item),
        "points": Decimal(item.estimate_value)
        if item.estimate_value is not None and item.estimate_unit == EstimationUnit.POINTS
        else None,
        "priority": item.priority.value,
        "type": itype.key.value if itype else "task",
        "impact": item.impact,
        "stage": (dims.get("stage") or [""])[0] or None,
        "tags": list(tags),
        "dimensions": dims,
    }


async def _schemes_for(
    session: AsyncSession, ws_id: str, item: m.WorkItem, dims: dict[str, list[str]]
) -> list[m.WeightScheme]:
    schemes = (
        await session.scalars(
            select(m.WeightScheme).where(
                m.WeightScheme.workspace_id == ws_id, m.WeightScheme.deleted_at.is_(None)
            )
        )
    ).all()
    out = []
    for sc in schemes:
        if sc.scope_type == ScopeType.WORKSPACE:
            out.append(sc)
        elif sc.scope_type == ScopeType.PROJECT and sc.scope_id == item.project_id:
            out.append(sc)
        elif sc.scope_type == ScopeType.TEAM and sc.scope_id == item.team_id:
            out.append(sc)
        elif sc.scope_type == ScopeType.PRODUCT and sc.scope_id:
            pv = await session.scalar(
                select(m.DimensionValue.id).where(m.DimensionValue.backing_id == sc.scope_id)
            )
            have = (
                (
                    await session.scalar(
                        select(m.Classification.id).where(
                            m.Classification.target_type == TargetType.WORK_ITEM,
                            m.Classification.target_id == item.id,
                            m.Classification.value_id == pv,
                            m.Classification.deleted_at.is_(None),
                        )
                    )
                )
                if pv
                else None
            )
            if have:
                out.append(sc)
    return out


async def recompute_item(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, *, actor_id: str | None = None
) -> None:
    """Recompute Weight rows for every applicable scheme (manual rows are skipped); mirror the default."""
    s = uow.session
    ctx = await item_context(s, item)
    default = await ensure_default_scheme(s, ws.id)
    actor = actor_id or uow.context.get("actor_id") or item.reporter_id
    for scheme in await _schemes_for(s, ws.id, item, ctx["dimensions"]):
        row = await s.scalar(
            select(m.Weight).where(
                m.Weight.work_item_id == item.id,
                m.Weight.scheme_id == scheme.id,
                m.Weight.deleted_at.is_(None),
            )
        )
        if row is not None and row.source == ValueSource.MANUAL:
            continue
        value = evaluate(scheme.formula or {}, ctx)
        if row is None:
            s.add(
                m.Weight(
                    id=new_id(),
                    work_item_id=item.id,
                    scheme_id=scheme.id,
                    value=value,
                    source=ValueSource.FORMULA,
                )
            )
            changed, old = True, None
        else:
            changed, old = row.value != value, row.value
            if changed:
                row.value, row.computed_at = value, utcnow()
        if changed and old is not None:  # the first computation is not an event
            emit(
                uow,
                workspace_id=ws.id,
                actor_id=actor,
                entity_type=TargetType.WORK_ITEM,
                entity_id=item.id,
                event_type="weight.recomputed",
                payload={
                    "before": {"value": str(old) if old is not None else None},
                    "after": {"value": str(value), "scheme": scheme.name},
                },
                source=EventSource.PROPAGATION,
                correlation_id=uow.context.get("correlation_id"),
            )
        if scheme.id == default.id:
            await _mirror_default(item, value, ctx)
    await s.flush()


async def _mirror_default(item: m.WorkItem, weight: Decimal, ctx: dict[str, Any]) -> None:
    item.weight = weight
    item.weight_scheme_id = None
    if item.impact is not None:
        hours = ctx["estimate_hours"] or Decimal(1)
        item.value_score = (Decimal(item.impact) * weight / max(hours, Decimal(1))).quantize(Q)
    else:
        item.value_score = None


async def set_override(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, scheme: m.WeightScheme, value: Decimal, *, by: str
) -> m.Weight:
    """Pin an item's weight under a scheme to a manual value and record the override."""
    s = uow.session
    if value < 0:
        raise Invalid("invalid_weight", "weight must not be negative", pointer="/value")
    row = await s.scalar(
        select(m.Weight).where(
            m.Weight.work_item_id == item.id, m.Weight.scheme_id == scheme.id, m.Weight.deleted_at.is_(None)
        )
    )
    if row is None:
        row = m.Weight(id=new_id(), work_item_id=item.id, scheme_id=scheme.id, value=value)
        s.add(row)
    before = row.value
    row.value, row.source, row.set_by, row.computed_at = value, ValueSource.MANUAL, by, utcnow()
    default = await ensure_default_scheme(s, ws.id)
    if scheme.id == default.id:
        await _mirror_default(item, value, await item_context(s, item))
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=by,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="weight.overridden",
        payload={"before": {"value": str(before)}, "after": {"value": str(value), "scheme": scheme.name}},
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    return row


async def list_schemes(uow: UnitOfWork, ws_id: str) -> list[m.WeightScheme]:
    """Live weight schemes of the workspace, seeding the default one first."""
    await ensure_default_scheme(uow.session, ws_id)
    return list(
        (
            await uow.session.scalars(
                select(m.WeightScheme).where(
                    m.WeightScheme.workspace_id == ws_id, m.WeightScheme.deleted_at.is_(None)
                )
            )
        ).all()
    )


async def create_scheme(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any]
) -> m.WeightScheme:
    """Create a scheme after validating its formula."""
    validate_formula(data["formula"])
    row = m.WeightScheme(id=new_id(), workspace_id=ws.id, **data)
    uow.session.add(row)
    await uow.session.flush()
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="weight.recomputed",
        payload={"after": {"scheme_created": row.name}},
    )
    return row


async def recompute_scheme(uow: UnitOfWork, ws: m.Workspace, actor_id: str, sid: str) -> int:
    """Recompute every live item of the workspace (the formula changed, or the scheme is new)."""
    sc = await uow.session.scalar(
        select(m.WeightScheme).where(m.WeightScheme.id == sid, m.WeightScheme.workspace_id == ws.id)
    )
    if sc is None:
        raise NotFound("not_found", "scheme not found")
    rows = (
        await uow.session.scalars(
            select(m.WorkItem)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .where(m.Project.workspace_id == ws.id, m.WorkItem.deleted_at.is_(None))
        )
    ).all()
    for it in rows:
        await recompute_item(uow, ws, it, actor_id=actor_id)
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="weight.recomputed",
        payload={"after": {"scheme": sc.name, "items": len(rows)}},
    )
    return len(rows)


async def find_scheme(uow: UnitOfWork, ws_id: str, ref: str) -> m.WeightScheme:
    """A live scheme by id or name; 404 when absent."""
    sc = await uow.session.scalar(
        select(m.WeightScheme).where(
            m.WeightScheme.workspace_id == ws_id,
            m.WeightScheme.deleted_at.is_(None),
            (m.WeightScheme.id == ref) | (m.WeightScheme.name == ref),
        )
    )
    if sc is None:
        raise NotFound("not_found", "scheme not found")
    return sc


async def item_weight_rows(uow: UnitOfWork, item: m.WorkItem) -> list[tuple[m.Weight, str]]:
    """The item's per-scheme weights as (row, scheme name)."""
    rows = (
        await uow.session.execute(
            select(m.Weight, m.WeightScheme.name)
            .join(m.WeightScheme, m.WeightScheme.id == m.Weight.scheme_id)
            .where(m.Weight.work_item_id == item.id, m.Weight.deleted_at.is_(None))
        )
    ).all()
    return [(w, n) for w, n in rows]
