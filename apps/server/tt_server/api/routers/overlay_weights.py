"""Overlay endpoints: weight schemes, recomputation and per-item weight overrides (Phase 3)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from tt_schema.enums import ScopeType

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.overlay_support import PREFIX, begin
from tt_server.api.routers.core import _item, row_dict
from tt_server.domain.overlay import weights

router = APIRouter(prefix=PREFIX)


class SchemeIn(BaseModel):
    name: str
    formula: dict[str, Any]
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None


@router.get("/weight-schemes", tags=["overlay"])
async def list_schemes(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = await weights.list_schemes(uow, actor.ws.id)
    await uow.commit()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/weight-schemes", status_code=201, tags=["overlay"], dependencies=[needs("weight.set")])
async def create_scheme(body: SchemeIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await weights.create_scheme(uow, actor.ws, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(row)


@router.post("/weight-schemes/{sid}/recompute", tags=["overlay"], dependencies=[needs("weight.set")])
async def recompute_scheme(sid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Recompute every item (the formula changed, or the scheme is new)."""
    begin(uow, actor)
    n = await weights.recompute_scheme(uow, actor.ws, actor.person.id, sid)
    await uow.commit()
    return {"recomputed": n}


@router.get("/items/{id_or_key}/weights", tags=["overlay"])
async def item_weights(id_or_key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    rows = await weights.item_weight_rows(uow, item)
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
    begin(uow, actor)
    item = await _item(uow, actor.ws.id, id_or_key)
    sc = await weights.find_scheme(uow, actor.ws.id, scheme)
    w = await weights.set_override(uow, actor.ws, item, sc, body.value, by=actor.person.id)
    await uow.commit()
    return row_dict(w)
