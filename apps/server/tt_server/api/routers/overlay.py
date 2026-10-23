"""Management overlay endpoints (Phase 3): dimensions, process, products, weights, risks, approvals."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    ApprovalStatus,
    Cardinality,
    Decision,
    DimensionKind,
    EventSource,
    Priority,
    ProductLinkRole,
    RiskCategory,
    RiskResponse,
    RiskStatus,
    ScopeType,
    TargetType,
    ValueSource,
    ValueTypeShape,
)

from tt_server.acl.visibility import item_visible_for, visible
from tt_server.api.authz import check, check_item, check_project, needs, needs_review
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _item, _project, row_dict
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.overlay import approvals, bulk, mirrors, products, propagation, review, risks, weights
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import rollup as rollup_svc

router = APIRouter(prefix="/api/v1/w/{ws}")
TARGETS = {"work_item": TargetType.WORK_ITEM, "project": TargetType.PROJECT, "product": TargetType.PRODUCT}


async def item_ref_for(uow: Uow, item: m.WorkItem) -> Any:
    from tt_server.acl.context import item_ref

    return await item_ref(uow.session, item)


def _begin(uow: Uow, actor: CurrentActor) -> None:
    uow.context["actor_id"] = actor.person.id
    uow.context.setdefault("correlation_id", new_id())


# ------------------------------------------------------------------ dimensions


class DimensionIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    name: str
    value_type: ValueTypeShape = ValueTypeShape.FLAT
    cardinality: Cardinality = Cardinality.SINGLE
    applies_to: list[TargetType] = Field(default_factory=lambda: [TargetType.WORK_ITEM])
    is_required_for: dict[str, Any] | None = None
    owner_role: str | None = None


@router.get("/dimensions", tags=["overlay"])
async def list_dimensions(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await mirrors.ensure_builtin_dimensions(uow.session, actor.ws.id)
    rows = (
        await uow.session.scalars(
            select(m.Dimension)
            .where(m.Dimension.workspace_id == actor.ws.id, m.Dimension.deleted_at.is_(None))
            .order_by(m.Dimension.key)
        )
    ).all()
    await uow.commit()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/dimensions", status_code=201, tags=["overlay"], dependencies=[needs("dimension.admin")])
async def create_dimension(body: DimensionIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    if await uow.session.scalar(
        select(m.Dimension.id).where(m.Dimension.workspace_id == actor.ws.id, m.Dimension.key == body.key)
    ):
        raise ApiError(
            409, "dimension_exists", f"dimension {body.key!r} exists (keys are immutable)", pointer="/key"
        )
    d = m.Dimension(id=new_id(), workspace_id=actor.ws.id, kind=DimensionKind.CUSTOM, **body.model_dump())
    d.applies_to = [t.value for t in body.applies_to]
    uow.session.add(d)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="dimension.created",
        payload={"after": {"key": d.key}},
    )
    await uow.commit()
    return row_dict(d)


async def _dimension(uow: Uow, ws_id: str, ref: str) -> m.Dimension:
    d = await uow.session.scalar(
        select(m.Dimension).where(
            m.Dimension.workspace_id == ws_id,
            m.Dimension.deleted_at.is_(None),
            (m.Dimension.id == ref) | (m.Dimension.key == ref),
        )
    )
    if d is None:
        raise ApiError(404, "not_found", "dimension not found")
    return d


class ValueIn(BaseModel):
    key: str
    name: str
    parent_value_id: str | None = None
    color: str | None = None
    position: int = 0


async def _would_cycle(uow: Uow, value_id: str, parent_id: str | None) -> bool:
    seen = {value_id}
    while parent_id:
        if parent_id in seen:
            return True
        seen.add(parent_id)
        parent_id = await uow.session.scalar(
            select(m.DimensionValue.parent_value_id).where(m.DimensionValue.id == parent_id)
        )
    return False


@router.get("/dimensions/{ref}/values", tags=["overlay"])
async def list_values(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    d = await _dimension(uow, actor.ws.id, ref)
    rows = (
        await uow.session.scalars(
            select(m.DimensionValue)
            .where(m.DimensionValue.dimension_id == d.id, m.DimensionValue.deleted_at.is_(None))
            .order_by(m.DimensionValue.position, m.DimensionValue.key)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post(
    "/dimensions/{ref}/values", status_code=201, tags=["overlay"], dependencies=[needs("dimension.admin")]
)
async def create_value(ref: str, body: ValueIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    d = await _dimension(uow, actor.ws.id, ref)
    if d.kind == DimensionKind.BUILTIN:
        raise ApiError(409, "builtin_dimension", "values of builtin dimensions follow their backing rows")
    if await uow.session.scalar(
        select(m.DimensionValue.id).where(
            m.DimensionValue.dimension_id == d.id,
            m.DimensionValue.key == body.key,
            m.DimensionValue.deleted_at.is_(None),
        )
    ):
        raise ApiError(409, "value_exists", f"value {body.key!r} exists", pointer="/key")
    if body.parent_value_id and d.value_type != ValueTypeShape.TREE:
        raise ApiError(400, "not_a_tree", "this dimension is flat", pointer="/parent_value_id")
    v = m.DimensionValue(id=new_id(), dimension_id=d.id, **body.model_dump())
    uow.session.add(v)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="dimension_value.created",
        payload={"after": {"dimension": d.key, "key": v.key}},
    )
    await uow.commit()
    return row_dict(v)


@router.post(
    "/dimensions/{ref}/values:bulk",
    status_code=201,
    tags=["overlay"],
    dependencies=[needs("dimension.admin")],
)
async def bulk_values(ref: str, names: list[str], actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    d = await _dimension(uow, actor.ws.id, ref)
    if d.kind == DimensionKind.BUILTIN:
        raise ApiError(409, "builtin_dimension", "values of builtin dimensions follow their backing rows")
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
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="dimension_value.created",
        payload={"after": {"dimension": d.key, "count": created}},
    )
    await uow.commit()
    return {"created": created}


class ValuePatch(BaseModel):
    name: str | None = None
    parent_value_id: str | None = None
    is_active: bool | None = None
    position: int | None = None


@router.patch(
    "/dimensions/{ref}/values/{value_id}", tags=["overlay"], dependencies=[needs("dimension.admin")]
)
async def patch_value(
    ref: str, value_id: str, body: ValuePatch, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    d = await _dimension(uow, actor.ws.id, ref)
    v = await uow.session.scalar(
        select(m.DimensionValue).where(m.DimensionValue.id == value_id, m.DimensionValue.dimension_id == d.id)
    )
    if v is None:
        raise ApiError(404, "not_found", "value not found")
    data = body.model_dump(exclude_unset=True)
    if "parent_value_id" in data and await _would_cycle(uow, v.id, data["parent_value_id"]):
        raise ApiError(400, "cycle", "a value cannot be its own ancestor", pointer="/parent_value_id")
    for k, val in data.items():
        setattr(v, k, val)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="dimension_value.updated",
        payload={"fields_changed": list(data)},
    )
    await uow.commit()
    return row_dict(v)


# ------------------------------------------------------------------ classifications


class ClassifyIn(BaseModel):
    target_type: TargetType
    target_id: str
    dimension: str
    value: str


async def _check_target(
    actor: CurrentActor, uow: Uow, permission: str, target_type: TargetType, target_id: str
) -> None:
    if target_type == TargetType.WORK_ITEM:
        item = await uow.session.get(m.WorkItem, target_id)
        if item is not None:
            await check_item(actor, uow, permission, item)
    elif target_type == TargetType.PROJECT:
        project = await uow.session.get(m.Project, target_id)
        if project is not None:
            await check_project(actor, uow, permission, project)
    else:
        check(actor, permission)


async def _target_id(uow: Uow, ws_id: str, target_type: TargetType, ref: str) -> str:
    if target_type == TargetType.WORK_ITEM:
        return (await _item(uow, ws_id, ref)).id
    if target_type == TargetType.PROJECT:
        return (await _project(uow, ws_id, ref)).id
    if target_type == TargetType.PRODUCT:
        p = await uow.session.scalar(
            select(m.Product).where(
                m.Product.workspace_id == ws_id, (m.Product.id == ref) | (m.Product.key == ref)
            )
        )
        if p is None:
            raise ApiError(404, "not_found", "product not found")
        return p.id
    return ref


@router.post("/classifications", status_code=201, tags=["overlay"])
async def classify(body: ClassifyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    dim = await _dimension(uow, actor.ws.id, body.dimension)
    value = await uow.session.scalar(
        select(m.DimensionValue).where(
            m.DimensionValue.dimension_id == dim.id,
            m.DimensionValue.deleted_at.is_(None),
            m.DimensionValue.is_active.is_(True),
            (m.DimensionValue.key == body.value) | (m.DimensionValue.id == body.value),
        )
    )
    if value is None:
        raise ApiError(404, "not_found", f"value {body.value!r} not found in {dim.key}", pointer="/value")
    tid = await _target_id(uow, actor.ws.id, body.target_type, body.target_id)
    await _check_target(actor, uow, "classification.write", body.target_type, tid)
    row = await classify_svc.classify(
        uow,
        actor.ws,
        target_type=body.target_type,
        target_id=tid,
        dimension=dim,
        value=value,
        source=ValueSource.MANUAL,
        set_by=actor.person.id,
    )
    item = await uow.session.get(m.WorkItem, tid) if body.target_type == TargetType.WORK_ITEM else None
    if item is not None:
        await weights.recompute_item(uow, actor.ws, item, actor_id=actor.person.id)
    await uow.commit()
    return row_dict(row) if row else {}


@router.get("/classifications", tags=["overlay"])
async def list_classifications(
    actor: CurrentActor,
    uow: Uow,
    target_type: TargetType = TargetType.WORK_ITEM,
    target: str | None = None,
    dimension: str | None = None,
    confirmed: bool | None = None,
) -> dict[str, Any]:
    stmt = (
        select(m.Classification, m.Dimension.key, m.DimensionValue.key)
        .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
        .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
        .where(
            m.Classification.workspace_id == actor.ws.id,
            m.Classification.target_type == target_type,
            m.Classification.deleted_at.is_(None),
        )
    )
    if target_type == TargetType.WORK_ITEM:  # only classifications of items the actor can see
        wi = aliased(m.WorkItem)
        stmt = stmt.where(
            m.Classification.target_id.in_(select(wi.id).where(item_visible_for(actor.acl, wi)))
        )
    if target:
        stmt = stmt.where(
            m.Classification.target_id == await _target_id(uow, actor.ws.id, target_type, target)
        )
    if dimension:
        stmt = stmt.where(m.Dimension.key == dimension)
    if confirmed is not None:
        stmt = stmt.where(m.Classification.is_confirmed.is_(confirmed))
    rows = (await uow.session.execute(stmt.limit(500))).all()
    return {
        "data": [{**row_dict(c), "dimension": dk, "value": vk} for c, dk, vk in rows],
        "next_cursor": None,
    }


@router.post("/classifications/{cid}/confirm", tags=["overlay"])
async def confirm_classification(cid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    c = await uow.session.scalar(
        select(m.Classification).where(
            m.Classification.id == cid, m.Classification.workspace_id == actor.ws.id
        )
    )
    if c is None:
        raise ApiError(404, "not_found", "classification not found")
    item = await uow.session.get(m.WorkItem, c.target_id) if c.target_type == TargetType.WORK_ITEM else None
    if item is not None and await _item(uow, actor.ws.id, item.id):  # invisible => 404
        assignee = item.assignee_id or item.reporter_id
        if not (
            actor.acl.is_line_manager_of(assignee)
            or actor.acl.can("classification.confirm", await item_ref_for(uow, item))
        ):
            raise ApiError(
                403,
                "forbidden",
                "missing permission 'classification.confirm'",
                details={"permission": "classification.confirm"},
            )
    else:
        check(actor, "classification.confirm")
    await classify_svc.confirm(uow, actor.ws, c, by=actor.person.id)
    await uow.commit()
    return row_dict(c)


@router.delete("/classifications/{cid}", status_code=204, tags=["overlay"])
async def delete_classification(cid: str, actor: CurrentActor, uow: Uow) -> Response:
    _begin(uow, actor)
    c = await uow.session.scalar(
        select(m.Classification).where(
            m.Classification.id == cid,
            m.Classification.workspace_id == actor.ws.id,
            m.Classification.deleted_at.is_(None),
        )
    )
    if c is None:
        raise ApiError(404, "not_found", "classification not found")
    await _check_target(actor, uow, "classification.write", c.target_type, c.target_id)
    await classify_svc.unclassify(uow, actor.ws, c, by=actor.person.id)
    await uow.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ process, stages, placements


@router.get("/processes", tags=["overlay"])
async def list_processes(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await proc.ensure_default_process(uow.session, actor.ws.id, actor.person.id)
    rows = (
        await uow.session.scalars(
            select(m.Process).where(m.Process.workspace_id == actor.ws.id, m.Process.deleted_at.is_(None))
        )
    ).all()
    out = []
    for p in rows:
        out.append({**row_dict(p), "stages": [row_dict(s) for s in await proc.stages_of(uow.session, p.id)]})
    await uow.commit()
    return {"data": out, "next_cursor": None}


class ProcessIn(BaseModel):
    key: str
    name: str
    description: str | None = None


class StageIn(BaseModel):
    key: str
    name: str
    category: str
    maps_to_state_category: str
    expected_duration_days: int | None = None
    wip_limit: int | None = None
    requires_approval: bool = False
    position: int | None = None


@router.post("/processes", status_code=201, tags=["overlay"], dependencies=[needs("process.admin")])
async def create_process(body: ProcessIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = m.Process(id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, **body.model_dump())
    uow.session.add(p)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="process.created",
        payload={"after": {"key": p.key}},
    )
    await uow.commit()
    return row_dict(p)


@router.post(
    "/processes/{ref}/stages", status_code=201, tags=["overlay"], dependencies=[needs("process.admin")]
)
async def add_stage(ref: str, body: StageIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    from tt_schema.enums import StageCategory, StateCategory

    p = await proc.get_process(uow.session, actor.ws.id, ref)
    pos = body.position if body.position is not None else len(await proc.stages_of(uow.session, p.id))
    data = body.model_dump(exclude={"position", "category", "maps_to_state_category"})
    try:
        st = m.Stage(
            id=new_id(),
            process_id=p.id,
            position=pos,
            category=StageCategory(body.category),
            maps_to_state_category=StateCategory(body.maps_to_state_category),
            **data,
        )
    except ValueError as exc:
        raise ApiError(400, "invalid_stage", str(exc)) from exc
    uow.session.add(st)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="stage.created",
        payload={"after": {"process": p.key, "key": st.key}},
    )
    await uow.commit()
    return row_dict(st)


@router.get("/processes/{ref}/board", tags=["overlay"])
async def process_board(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await proc.board(
        uow.session, actor.ws.id, await proc.get_process(uow.session, actor.ws.id, ref), acl=actor.acl
    )


class StageMove(BaseModel):
    stage: str
    process: str | None = None
    reason: str | None = None


@router.put(
    "/items/{id_or_key}/stage", tags=["overlay"], dependencies=[needs("item.write_overlay", item="id_or_key")]
)
async def move_stage(id_or_key: str, body: StageMove, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    item = await _item(uow, actor.ws.id, id_or_key)
    process = await proc.get_process(uow.session, actor.ws.id, body.process)
    stage = await proc.get_stage(uow.session, process, body.stage)
    pl = await proc.place(
        uow,
        actor.ws,
        target_type=TargetType.WORK_ITEM,
        target_id=item.id,
        process=process,
        stage=stage,
        moved_by=actor.person.id,
        reason=body.reason,
    )
    await weights.recompute_item(uow, actor.ws, item, actor_id=actor.person.id)
    await uow.commit()
    return row_dict(pl)


@router.get("/items/{id_or_key}/placements", tags=["overlay"])
async def placements(id_or_key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    rows = (
        await uow.session.execute(
            select(m.StagePlacement, m.Stage.key)
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .where(
                m.StagePlacement.target_type == TargetType.WORK_ITEM, m.StagePlacement.target_id == item.id
            )
            .order_by(m.StagePlacement.entered_at)
        )
    ).all()
    return {"data": [{**row_dict(p), "stage": k} for p, k in rows], "next_cursor": None}


# ------------------------------------------------------------------ products & objectives


class ProductIn(BaseModel):
    key: str
    name: str
    owner_id: str | None = None
    parent_product_id: str | None = None
    description: str | None = None


@router.post("/products", status_code=201, tags=["overlay"], dependencies=[needs("product.admin")])
async def create_product(body: ProductIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    p = await products.create_product(
        uow,
        actor.ws,
        key=body.key,
        name=body.name,
        owner_id=body.owner_id or actor.person.id,
        actor_id=actor.person.id,
        parent_product_id=body.parent_product_id,
        description=body.description,
    )
    await uow.commit()
    return row_dict(p)


@router.get("/products", tags=["overlay"])
async def list_products(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.Product)
            .where(m.Product.workspace_id == actor.ws.id, m.Product.deleted_at.is_(None))
            .order_by(m.Product.key)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


async def _product(uow: Uow, ws_id: str, ref: str) -> m.Product:
    p = await uow.session.scalar(
        select(m.Product).where(
            m.Product.workspace_id == ws_id,
            m.Product.deleted_at.is_(None),
            (m.Product.id == ref) | (m.Product.key == ref.upper()),
        )
    )
    if p is None:
        raise ApiError(404, "not_found", "product not found")
    return p


class ProductPatch(BaseModel):
    name: str | None = None
    parent_product_id: str | None = None
    description: str | None = None


@router.patch("/products/{ref}", tags=["overlay"], dependencies=[needs("product.admin")])
async def patch_product(ref: str, body: ProductPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    p = await _product(uow, actor.ws.id, ref)
    data = body.model_dump(exclude_unset=True)
    if "parent_product_id" in data:
        await products.reparent(uow, actor.ws, p, data.pop("parent_product_id"), actor_id=actor.person.id)
    for k, v in data.items():
        setattr(p, k, v)
    await uow.commit()
    return row_dict(p)


class LinkIn(BaseModel):
    target_type: TargetType
    target_id: str
    role: ProductLinkRole = ProductLinkRole.DELIVERS
    weight: float | None = None


@router.post(
    "/products/{ref}/links", status_code=201, tags=["overlay"], dependencies=[needs("product.admin")]
)
async def link_product(ref: str, body: LinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    p = await _product(uow, actor.ws.id, ref)
    tid = await _target_id(uow, actor.ws.id, body.target_type, body.target_id)
    row = await products.link(
        uow,
        actor.ws,
        p,
        target_type=body.target_type,
        target_id=tid,
        role=body.role,
        actor_id=actor.person.id,
        weight=body.weight,
    )
    await uow.commit()
    return row_dict(row)


@router.get("/products/{ref}/links", tags=["overlay"])
async def product_links(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _product(uow, actor.ws.id, ref)
    rows = (
        await uow.session.scalars(
            select(m.ProductLink).where(m.ProductLink.product_id == p.id, m.ProductLink.deleted_at.is_(None))
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class PriorityIn(BaseModel):
    priority: Priority


@router.put(
    "/projects/{key}/priority", tags=["overlay"], dependencies=[needs("item.write_overlay", project="key")]
)
async def set_project_priority(key: str, body: PriorityIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    p = await _project(uow, actor.ws.id, key)
    res = await propagation.set_container_priority(
        uow,
        actor.ws,
        TargetType.PROJECT,
        p.id,
        body.priority,
        actor=actor.person.id,
        correlation_id=uow.context["correlation_id"],
    )
    await uow.commit()
    return {
        "priority": body.priority.value,
        "items_changed": res.changed,
        "overrides_kept": res.kept_manual,
        "correlation_id": uow.context["correlation_id"],
    }


@router.put("/products/{ref}/priority", tags=["overlay"], dependencies=[needs("product.admin")])
async def set_product_priority(ref: str, body: PriorityIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    p = await _product(uow, actor.ws.id, ref)
    res = await propagation.set_container_priority(
        uow,
        actor.ws,
        TargetType.PRODUCT,
        p.id,
        body.priority,
        actor=actor.person.id,
        correlation_id=uow.context["correlation_id"],
    )
    await uow.commit()
    return {"priority": body.priority.value, "items_changed": res.changed, "overrides_kept": res.kept_manual}


class ObjectiveIn(BaseModel):
    scope_type: ScopeType
    scope_id: str
    title: str
    period: str
    owner_id: str | None = None


@router.post("/objectives", status_code=201, tags=["overlay"], dependencies=[needs("objective.write")])
async def create_objective(body: ObjectiveIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    o = await products.create_objective(
        uow,
        actor.ws,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        title=body.title,
        period=body.period,
        owner_id=body.owner_id or actor.person.id,
        actor_id=actor.person.id,
    )
    await uow.commit()
    return row_dict(o)


@router.get("/objectives", tags=["overlay"])
async def list_objectives(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.Objective).where(
                m.Objective.workspace_id == actor.ws.id, m.Objective.deleted_at.is_(None)
            )
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class KeyResultIn(BaseModel):
    title: str
    target_value: Decimal
    unit: str
    current_value: Decimal = Decimal(0)
    metric_key: str | None = None
    source: str = "manual"


@router.post(
    "/objectives/{oid}/key-results",
    status_code=201,
    tags=["overlay"],
    dependencies=[needs("objective.write")],
)
async def add_key_result(oid: str, body: KeyResultIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    o = await uow.session.scalar(
        select(m.Objective).where(m.Objective.id == oid, m.Objective.workspace_id == actor.ws.id)
    )
    if o is None:
        raise ApiError(404, "not_found", "objective not found")
    kr = m.KeyResult(id=new_id(), objective_id=o.id, **body.model_dump())
    uow.session.add(kr)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.OBJECTIVE,
        entity_id=o.id,
        event_type="key_result.updated",
        payload={"after": {"key_result_id": kr.id, "current": str(kr.current_value)}},
    )
    await uow.commit()
    return row_dict(kr)


@router.patch(
    "/objectives/{oid}/key-results/{kid}", tags=["overlay"], dependencies=[needs("objective.write")]
)
async def update_key_result(
    oid: str, kid: str, current_value: Decimal, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    kr = await uow.session.scalar(
        select(m.KeyResult)
        .join(m.Objective, m.Objective.id == m.KeyResult.objective_id)
        .where(m.KeyResult.id == kid, m.Objective.id == oid, m.Objective.workspace_id == actor.ws.id)
    )
    if kr is None:
        raise ApiError(404, "not_found", "key result not found")
    before = kr.current_value
    kr.current_value = current_value
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.OBJECTIVE,
        entity_id=oid,
        event_type="key_result.updated",
        payload={"before": {"current": str(before)}, "after": {"current": str(current_value)}},
    )
    await uow.commit()
    return row_dict(kr)


@router.get("/objectives/{oid}/key-results", tags=["overlay"])
async def list_key_results(oid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.KeyResult)
            .join(m.Objective, m.Objective.id == m.KeyResult.objective_id)
            .where(m.Objective.id == oid, m.Objective.workspace_id == actor.ws.id)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


# ------------------------------------------------------------------ weights


class SchemeIn(BaseModel):
    name: str
    formula: dict[str, Any]
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None


@router.get("/weight-schemes", tags=["overlay"])
async def list_schemes(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await weights.ensure_default_scheme(uow.session, actor.ws.id)
    rows = (
        await uow.session.scalars(
            select(m.WeightScheme).where(
                m.WeightScheme.workspace_id == actor.ws.id, m.WeightScheme.deleted_at.is_(None)
            )
        )
    ).all()
    await uow.commit()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/weight-schemes", status_code=201, tags=["overlay"], dependencies=[needs("weight.set")])
async def create_scheme(body: SchemeIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    weights.validate_formula(body.formula)
    row = m.WeightScheme(id=new_id(), workspace_id=actor.ws.id, **body.model_dump())
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="weight.recomputed",
        payload={"after": {"scheme_created": row.name}},
    )
    await uow.commit()
    return row_dict(row)


@router.post("/weight-schemes/{sid}/recompute", tags=["overlay"], dependencies=[needs("weight.set")])
async def recompute_scheme(sid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Recompute every item (the formula changed, or the scheme is new)."""
    _begin(uow, actor)
    sc = await uow.session.scalar(
        select(m.WeightScheme).where(m.WeightScheme.id == sid, m.WeightScheme.workspace_id == actor.ws.id)
    )
    if sc is None:
        raise ApiError(404, "not_found", "scheme not found")
    rows = (
        await uow.session.scalars(
            select(m.WorkItem)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .where(m.Project.workspace_id == actor.ws.id, m.WorkItem.deleted_at.is_(None))
        )
    ).all()
    for it in rows:
        await weights.recompute_item(uow, actor.ws, it, actor_id=actor.person.id)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="weight.recomputed",
        payload={"after": {"scheme": sc.name, "items": len(rows)}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return {"recomputed": len(rows)}


@router.get("/items/{id_or_key}/weights", tags=["overlay"])
async def item_weights(id_or_key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    rows = (
        await uow.session.execute(
            select(m.Weight, m.WeightScheme.name)
            .join(m.WeightScheme, m.WeightScheme.id == m.Weight.scheme_id)
            .where(m.Weight.work_item_id == item.id, m.Weight.deleted_at.is_(None))
        )
    ).all()
    return {
        "data": [{**row_dict(w), "scheme": n} for w, n in rows],
        "item_weight": str(item.weight),
        "value_score": str(item.value_score) if item.value_score is not None else None,
        "next_cursor": None,
    }


class WeightIn(BaseModel):
    value: Decimal


@router.put(
    "/items/{id_or_key}/weights/{scheme}",
    tags=["overlay"],
    dependencies=[needs("weight.set", item="id_or_key")],
)
async def override_weight(
    id_or_key: str, scheme: str, body: WeightIn, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    _begin(uow, actor)
    item = await _item(uow, actor.ws.id, id_or_key)
    sc = await uow.session.scalar(
        select(m.WeightScheme).where(
            m.WeightScheme.workspace_id == actor.ws.id,
            m.WeightScheme.deleted_at.is_(None),
            (m.WeightScheme.id == scheme) | (m.WeightScheme.name == scheme),
        )
    )
    if sc is None:
        raise ApiError(404, "not_found", "scheme not found")
    w = await weights.set_override(uow, actor.ws, item, sc, body.value, by=actor.person.id)
    await uow.commit()
    return row_dict(w)


# ------------------------------------------------------------------ risks


class RiskIn(BaseModel):
    project: str
    title: str
    target_type: TargetType = TargetType.PROJECT
    target_id: str | None = None
    category: RiskCategory = RiskCategory.TECHNICAL
    probability: int = 3
    impact: int = 3
    response_strategy: RiskResponse = RiskResponse.MITIGATE
    description: str | None = None
    owner_id: str | None = None


@router.post("/risks", status_code=201, tags=["overlay"])
async def create_risk(body: RiskIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    p = await _project(uow, actor.ws.id, body.project)
    await check_project(actor, uow, "risk.write", p)
    tid = body.target_id
    if tid and body.target_type == TargetType.WORK_ITEM:
        tid = (await _item(uow, actor.ws.id, tid)).id
    r = await risks.create_risk(
        uow,
        actor.ws,
        p,
        title=body.title,
        owner_id=body.owner_id or actor.person.id,
        actor_id=actor.person.id,
        target_type=body.target_type,
        target_id=tid,
        category=body.category,
        probability=body.probability,
        impact=body.impact,
        response_strategy=body.response_strategy,
        description=body.description,
    )
    await uow.commit()
    return {**row_dict(r), "score": r.probability * r.impact}


@router.get("/risks", tags=["overlay"])
async def list_risks(
    actor: CurrentActor, uow: Uow, project: str | None = None, open_only: bool = False
) -> dict[str, Any]:
    stmt = (
        select(m.Risk, m.Project.key)
        .join(m.Project, m.Project.id == m.Risk.project_id)
        .where(m.Project.workspace_id == actor.ws.id, m.Risk.deleted_at.is_(None))
    )
    stmt = visible(stmt, actor.acl, m.Risk)
    if project:
        stmt = stmt.where(func.lower(m.Project.key) == project.lower())
    if open_only:
        stmt = stmt.where(m.Risk.status.in_(risks.OPEN_STATUSES))
    rows = (await uow.session.execute(stmt.order_by(m.Risk.key))).all()
    return {
        "data": [{**row_dict(r), "score": r.probability * r.impact, "project_key": k} for r, k in rows],
        "next_cursor": None,
    }


async def _risk(uow: Uow, ws_id: str, ref: str, actor: CurrentActor | None = None) -> m.Risk:
    stmt = (
        select(m.Risk)
        .join(m.Project, m.Project.id == m.Risk.project_id)
        .where(
            m.Project.workspace_id == ws_id,
            m.Risk.deleted_at.is_(None),
            (m.Risk.id == ref) | (m.Risk.key == ref.upper()),
        )
    )
    if (acl := uow.context.get("acl")) is not None:
        stmt = visible(stmt, acl, m.Risk)
    r = await uow.session.scalar(stmt)
    if r is None:
        raise ApiError(404, "not_found", "risk not found")
    if actor is not None:  # writers need risk.write on the risk's project
        await check_project(actor, uow, "risk.write", await uow.session.get(m.Project, r.project_id))  # type: ignore[arg-type]
    return r


class RiskPatch(BaseModel):
    status: RiskStatus | None = None
    probability: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    title: str | None = None
    review_date: date | None = None


@router.patch("/risks/{ref}", tags=["overlay"])
async def patch_risk(ref: str, body: RiskPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    r = await _risk(uow, actor.ws.id, ref, actor)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.RISK,
        entity_id=r.id,
        event_type="risk.updated",
        payload={"fields_changed": list(data)},
    )
    await uow.commit()
    return {**row_dict(r), "score": r.probability * r.impact}


class RiskLinkIn(BaseModel):
    item: str


@router.post("/risks/{ref}/links", status_code=201, tags=["overlay"])
async def link_risk(ref: str, body: RiskLinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    r = await _risk(uow, actor.ws.id, ref, actor)
    item = await _item(uow, actor.ws.id, body.item)
    n = await risks.link_item(uow, actor.ws, r, item, actor_id=actor.person.id)
    await uow.commit()
    return {"risk": r.key, "item": item.key, "inherited_links_created": n}


@router.delete("/risks/{ref}/links/{item_ref}", tags=["overlay"])
async def unlink_risk(ref: str, item_ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    r = await _risk(uow, actor.ws.id, ref, actor)
    item = await _item(uow, actor.ws.id, item_ref)
    n = await risks.unlink_item(uow, actor.ws, r, item, actor_id=actor.person.id)
    await uow.commit()
    return {"risk": r.key, "item": item.key, "inherited_links_removed": n}


class ActionIn(BaseModel):
    description: str
    due_date: date | None = None


@router.post("/risks/{ref}/actions", status_code=201, tags=["overlay"])
async def add_risk_action(ref: str, body: ActionIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    r = await _risk(uow, actor.ws.id, ref, actor)
    a = await risks.add_action(
        uow, actor.ws, r, description=body.description, actor_id=actor.person.id, due_date=body.due_date
    )
    await uow.commit()
    return row_dict(a)


@router.post("/risks/{ref}/actions/{aid}/create-item", status_code=201, tags=["overlay"])
async def action_to_item(ref: str, aid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    r = await _risk(uow, actor.ws.id, ref, actor)
    a = await uow.session.scalar(
        select(m.RiskAction).where(m.RiskAction.id == aid, m.RiskAction.risk_id == r.id)
    )
    if a is None:
        raise ApiError(404, "not_found", "action not found")
    if a.work_item_id:
        raise ApiError(409, "already_linked", "this action already has a work item")
    project = await uow.session.get(m.Project, r.project_id)
    assert project is not None  # noqa: S101
    item = await items.create_item(
        uow,
        actor.ws,
        project,
        title=a.description,
        reporter_id=actor.person.id,
        assignee_id=actor.person.id,
        due_date=a.due_date,
    )
    a.work_item_id = item.id
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.created",
        payload={"after": {"key": item.key, "from_risk_action": a.id}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return row_dict(item)


# ------------------------------------------------------------------ approvals


@router.get("/approvals", tags=["overlay"])
async def list_approvals(
    actor: CurrentActor, uow: Uow, status: ApprovalStatus | None = None, mine: bool = False
) -> dict[str, Any]:
    stmt = (
        select(m.Approval)
        .join(m.Project, m.Project.id == m.Approval.project_id)
        .where(m.Project.workspace_id == actor.ws.id, m.Approval.deleted_at.is_(None))
    )
    stmt = visible(stmt, actor.acl, m.Project)
    if status:
        stmt = stmt.where(m.Approval.status == status)
    if mine:
        stmt = stmt.join(m.ApprovalDecision, m.ApprovalDecision.approval_id == m.Approval.id).where(
            m.ApprovalDecision.approver_id == actor.person.id, m.ApprovalDecision.decision == Decision.PENDING
        )
    rows = (await uow.session.scalars(stmt.order_by(m.Approval.requested_at.desc()))).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class DecisionIn(BaseModel):
    decision: Decision
    comment: str | None = None


@router.post("/approvals/{aid}/decisions", status_code=201, tags=["overlay"])
async def decide(aid: str, body: DecisionIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    a = await uow.session.scalar(
        select(m.Approval)
        .join(m.Project, m.Project.id == m.Approval.project_id)
        .where(m.Approval.id == aid, m.Project.workspace_id == actor.ws.id)
    )
    if a is None:
        raise ApiError(404, "not_found", "approval not found")
    await check_project(actor, uow, "approval.decide", await uow.session.get(m.Project, a.project_id))  # type: ignore[arg-type]
    await approvals.decide(
        uow, actor.ws, a, approver_id=actor.person.id, decision=body.decision, comment=body.comment
    )
    await uow.commit()
    return row_dict(a)


# ------------------------------------------------------------------ review queue


@router.get("/review-queue", tags=["review"], dependencies=[needs_review()])
async def review_queue(actor: CurrentActor, uow: Uow, kind: str | None = None) -> dict[str, Any]:
    return {
        "data": await review.list_queue(uow.session, actor.ws.id, actor.person.id, kind),
        "next_cursor": None,
    }


class ResolveIn(BaseModel):
    ref: str
    choice: dict[str, Any] = Field(default_factory=dict)


@router.post("/review-queue/resolve", tags=["review"], dependencies=[needs_review()])
async def resolve_review(body: ResolveIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    out = await review.resolve(uow, actor.ws, body.ref, body.choice, actor_id=actor.person.id)
    await uow.commit()
    return out


class BulkReviewIn(BaseModel):
    refs: list[str]
    action: str = "ignore"


@router.post("/review-queue/bulk", tags=["review"], dependencies=[needs_review()])
async def bulk_review(body: BulkReviewIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    out = await review.bulk(uow, actor.ws, body.refs, body.action, actor_id=actor.person.id)
    await uow.commit()
    return out


@router.post("/review-queue/confirm-all-suggested", tags=["review"], dependencies=[needs_review()])
async def confirm_all(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    n = await review.confirm_all_suggested(uow, actor.ws, actor.person.id)
    await uow.commit()
    return {"confirmed": n}


# ------------------------------------------------------------------ bulk edit, provenance, rollup, collisions


class BulkEdit(BaseModel):
    ids: list[str] | None = None
    filter: dict[str, Any] | None = None
    set: dict[str, Any]


@router.post("/items/bulk-preview", tags=["overlay"])
async def bulk_preview(body: BulkEdit, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    targets = await bulk.resolve_targets(uow, actor.ws, ids=body.ids, filt=body.filter)
    return await bulk.preview(uow, actor.ws, targets, body.set)


@router.post("/items/bulk", tags=["overlay"])
async def bulk_apply(body: BulkEdit, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _begin(uow, actor)
    targets = await bulk.resolve_targets(uow, actor.ws, ids=body.ids, filt=body.filter)
    for t in targets:
        await check_item(actor, uow, "item.write_overlay", t)
    out = await bulk.apply(uow, actor.ws, targets, body.set, actor=actor.person.id)
    await uow.commit()
    return out


@router.get("/items/{id_or_key}/provenance", tags=["overlay"])
async def provenance(id_or_key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await rollup_svc.provenance(uow.session, await _item(uow, actor.ws.id, id_or_key))


@router.get("/items/{id_or_key}/rollup", tags=["overlay"])
async def item_rollup(id_or_key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await rollup_svc.rollup(uow.session, await _item(uow, actor.ws.id, id_or_key))


class ItemPatch(BaseModel):
    description: str | None = None
    severity: str | None = None
    impact: int | None = Field(default=None, ge=1, le=5)
    priority: Priority | None = None
    planned_start: date | None = None
    due_date: date | None = None
    team_id: str | None = None
    assignee_id: str | None = None


def etag_of(item: m.WorkItem) -> str:
    return f'"{item.updated_at.isoformat()}"'


@router.patch(
    "/items/{id_or_key}", tags=["overlay"], dependencies=[needs("item.write_overlay", item="id_or_key")]
)
async def patch_item(
    id_or_key: str,
    body: ItemPatch,
    response: Response,
    actor: CurrentActor,
    uow: Uow,
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    """Overlay edit with optimistic concurrency (UX §3.4): `If-Match` carries the ETag you read."""
    _begin(uow, actor)
    item = await _item(uow, actor.ws.id, id_or_key)
    data = body.model_dump(exclude_unset=True)
    if if_match is None:
        raise ApiError(428, "precondition_required", "send If-Match with the ETag you read")
    if if_match != etag_of(item):
        emit(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            entity_type=TargetType.WORK_ITEM,
            entity_id=item.id,
            event_type="item.field_conflict",
            payload={"fields_attempted": sorted(data)},
            source=EventSource.OVERLAY,
        )
        current = {k: getattr(item, k) for k in data}
        await uow.commit()  # keep the conflict event even though the write is refused
        raise ApiError(
            409,
            "conflict",
            "the item changed since you read it",
            details={
                "current": {
                    k: (v.value if hasattr(v, "value") else (v.isoformat() if hasattr(v, "isoformat") else v))
                    for k, v in current.items()
                },
                "etag": etag_of(item),
            },
        )
    priority = data.pop("priority", None)
    changed = list(data)
    for k, v in data.items():
        setattr(item, k, v)
    if "impact" in data:
        emit(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            entity_type=TargetType.WORK_ITEM,
            entity_id=item.id,
            event_type="item.impact_changed",
            payload={"after": {"impact": item.impact}},
            source=EventSource.OVERLAY,
        )
    if "team_id" in data:
        await mirrors.sync_item_mirrors(uow, actor.ws, item, set_by=actor.person.id)
    if priority is not None and priority != item.priority:
        await propagation.set_item_priority(uow, actor.ws, item, priority, actor=actor.person.id)
        changed.append("priority")
    if changed:
        await weights.recompute_item(uow, actor.ws, item, actor_id=actor.person.id)
        emit(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            entity_type=TargetType.WORK_ITEM,
            entity_id=item.id,
            event_type="item.updated",
            payload={"fields_changed": sorted(changed)},
            source=EventSource.OVERLAY,
        )
    await uow.session.flush()
    await uow.session.refresh(item)
    await uow.commit()
    response.headers["ETag"] = etag_of(item)
    _ = utcnow
    return row_dict(item)
