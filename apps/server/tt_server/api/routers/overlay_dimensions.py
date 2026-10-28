"""Overlay endpoints: dimensions, their values and classifications (Phase 3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from tt_schema.enums import Cardinality, TargetType, ValueTypeShape

from tt_server.api.authz import check, needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.overlay_support import PREFIX, begin, check_target, item_ref_for, target_id_of
from tt_server.api.routers.core import _item
from tt_server.db import models as m
from tt_server.domain.common import row_dict
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import dimensions as dims

router = APIRouter(prefix=PREFIX)


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
    rows = await dims.list_dimensions(uow, actor.ws.id)
    await uow.commit()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/dimensions", status_code=201, tags=["overlay"], dependencies=[needs("dimension.admin")])
async def create_dimension(body: DimensionIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    d = await dims.create_dimension(uow, actor.ws, actor.person.id, body.model_dump(), body.applies_to)
    await uow.commit()
    return row_dict(d)


class ValueIn(BaseModel):
    __module__ = "tt_server.api.routers.overlay"  # pins the published OpenAPI schema name

    key: str
    name: str
    parent_value_id: str | None = None
    color: str | None = None
    position: int = 0


@router.get("/dimensions/{ref}/values", tags=["overlay"])
async def list_values(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    d = await dims.get_dimension(uow, actor.ws.id, ref)
    return {"data": [row_dict(r) for r in await dims.list_values(uow, d)], "next_cursor": None}


@router.post(
    "/dimensions/{ref}/values", status_code=201, tags=["overlay"], dependencies=[needs("dimension.admin")]
)
async def create_value(ref: str, body: ValueIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    d = await dims.get_dimension(uow, actor.ws.id, ref)
    v = await dims.create_value(uow, actor.ws, actor.person.id, d, body.model_dump())
    await uow.commit()
    return row_dict(v)


@router.post(
    "/dimensions/{ref}/values:bulk",
    status_code=201,
    tags=["overlay"],
    dependencies=[needs("dimension.admin")],
)
async def bulk_values(ref: str, names: list[str], actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    d = await dims.get_dimension(uow, actor.ws.id, ref)
    created = await dims.bulk_values(uow, actor.ws, actor.person.id, d, names)
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
    d = await dims.get_dimension(uow, actor.ws.id, ref)
    v = await dims.patch_value(
        uow, actor.ws, actor.person.id, d, value_id, body.model_dump(exclude_unset=True)
    )
    await uow.commit()
    return row_dict(v)


# ------------------------------------------------------------------ classifications


class ClassifyIn(BaseModel):
    target_type: TargetType
    target_id: str
    dimension: str
    value: str


@router.post("/classifications", status_code=201, tags=["overlay"])
async def classify(body: ClassifyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    dim = await dims.get_dimension(uow, actor.ws.id, body.dimension)
    value = await dims.find_value(uow, dim, body.value)
    tid = await target_id_of(uow, actor.ws.id, body.target_type, body.target_id)
    await check_target(actor, uow, "classification.write", body.target_type, tid)
    row = await dims.classify_manually(
        uow,
        actor.ws,
        actor.person.id,
        target_type=body.target_type,
        target_id=tid,
        dimension=dim,
        value=value,
    )
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
    tid = await target_id_of(uow, actor.ws.id, target_type, target) if target else None
    rows = await dims.classification_rows(
        uow, actor.ws.id, actor.acl, target_type, target_id=tid, dimension=dimension, confirmed=confirmed
    )
    return {
        "data": [{**row_dict(c), "dimension": dk, "value": vk} for c, dk, vk in rows],
        "next_cursor": None,
    }


@router.post("/classifications/{cid}/confirm", tags=["overlay"])
async def confirm_classification(cid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    c = await dims.get_classification(uow, actor.ws.id, cid)
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
    begin(uow, actor)
    c = await dims.get_classification(uow, actor.ws.id, cid, live_only=True)
    await check_target(actor, uow, "classification.write", c.target_type, c.target_id)
    await classify_svc.unclassify(uow, actor.ws, c, by=actor.person.id)
    await uow.commit()
    return Response(status_code=204)
