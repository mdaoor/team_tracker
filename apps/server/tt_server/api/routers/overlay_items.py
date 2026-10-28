"""Overlay endpoints on work items: bulk edit, provenance, rollup and the optimistic-concurrency patch."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field
from tt_schema.enums import Priority

from tt_server.api.authz import check_item, needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.overlay_support import PREFIX, begin
from tt_server.api.routers.core import _item
from tt_server.domain.common import row_dict
from tt_server.domain.items import patch as item_patch
from tt_server.domain.overlay import bulk
from tt_server.domain.overlay import rollup as rollup_svc

router = APIRouter(prefix=PREFIX)
etag_of = item_patch.etag_of


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
    begin(uow, actor)
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
    return await rollup_svc.rollup(uow.session, await _item(uow, actor.ws.id, id_or_key), acl=actor.acl)


class ItemPatch(BaseModel):
    description: str | None = None
    severity: str | None = None
    impact: int | None = Field(default=None, ge=1, le=5)
    priority: Priority | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    due_date: date | None = None
    team_id: str | None = None
    assignee_id: str | None = None


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
    begin(uow, actor)
    item = await _item(uow, actor.ws.id, id_or_key)
    await item_patch.patch_overlay(
        uow, actor.ws, actor.person.id, item, body.model_dump(exclude_unset=True), if_match=if_match
    )
    await uow.commit()
    response.headers["ETag"] = etag_of(item)
    return row_dict(item)
