"""Overlay endpoints: products, product links, container priorities, objectives and key results (Phase 3)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from tt_schema.enums import Priority, ProductLinkRole, ScopeType, TargetType

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.overlay_support import PREFIX, begin, target_id_of
from tt_server.api.routers.core import _project
from tt_server.domain.common import row_dict
from tt_server.domain.overlay import objectives, products, propagation

router = APIRouter(prefix=PREFIX)


class ProductIn(BaseModel):
    key: str
    name: str
    owner_id: str | None = None
    parent_product_id: str | None = None
    description: str | None = None


@router.post("/products", status_code=201, tags=["overlay"], dependencies=[needs("product.admin")])
async def create_product(body: ProductIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
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
    rows = await products.list_products(uow, actor.ws.id, actor.acl)
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class ProductPatch(BaseModel):
    name: str | None = None
    parent_product_id: str | None = None
    description: str | None = None


@router.patch("/products/{ref}", tags=["overlay"], dependencies=[needs("product.admin")])
async def patch_product(ref: str, body: ProductPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    p = await products.get_product(uow, actor.ws.id, ref)
    await products.patch_product(uow, actor.ws, actor.person.id, p, body.model_dump(exclude_unset=True))
    await uow.commit()
    return row_dict(p)


class LinkIn(BaseModel):
    __module__ = "tt_server.api.routers.overlay"  # pins the published OpenAPI schema name

    target_type: TargetType
    target_id: str
    role: ProductLinkRole = ProductLinkRole.DELIVERS
    weight: float | None = None


LinkIn.model_rebuild(_types_namespace={"TargetType": TargetType, "ProductLinkRole": ProductLinkRole})


@router.post(
    "/products/{ref}/links", status_code=201, tags=["overlay"], dependencies=[needs("product.admin")]
)
async def link_product(ref: str, body: LinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    p = await products.get_product(uow, actor.ws.id, ref)
    tid = await target_id_of(uow, actor.ws.id, body.target_type, body.target_id)
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
    p = await products.get_product(uow, actor.ws.id, ref)
    return {"data": [row_dict(r) for r in await products.list_links(uow, p)], "next_cursor": None}


class PriorityIn(BaseModel):
    priority: Priority


@router.put(
    "/projects/{key}/priority", tags=["overlay"], dependencies=[needs("item.write_overlay", project="key")]
)
async def set_project_priority(key: str, body: PriorityIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
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
    begin(uow, actor)
    p = await products.get_product(uow, actor.ws.id, ref)
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


# ------------------------------------------------------------------ objectives


class ObjectiveIn(BaseModel):
    scope_type: ScopeType
    scope_id: str
    title: str
    period: str
    owner_id: str | None = None


@router.post("/objectives", status_code=201, tags=["overlay"], dependencies=[needs("objective.write")])
async def create_objective(body: ObjectiveIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
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
    return {
        "data": [row_dict(r) for r in await objectives.list_objectives(uow, actor.ws.id)],
        "next_cursor": None,
    }


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
    kr = await objectives.add_key_result(uow, actor.ws, actor.person.id, oid, body.model_dump())
    await uow.commit()
    return row_dict(kr)


@router.patch(
    "/objectives/{oid}/key-results/{kid}", tags=["overlay"], dependencies=[needs("objective.write")]
)
async def update_key_result(
    oid: str, kid: str, current_value: Decimal, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    kr = await objectives.update_key_result(uow, actor.ws, actor.person.id, oid, kid, current_value)
    await uow.commit()
    return row_dict(kr)


@router.get("/objectives/{oid}/key-results", tags=["overlay"])
async def list_key_results(oid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = await objectives.list_key_results(uow, actor.ws.id, oid)
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}
