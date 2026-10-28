"""Overlay endpoints: processes, stages, the board and an item's stage placements (Phase 3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.overlay_support import PREFIX, begin
from tt_server.api.routers.core import _item
from tt_server.domain.common import row_dict
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import stages

router = APIRouter(prefix=PREFIX)


@router.get("/processes", tags=["overlay"])
async def list_processes(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = await stages.list_processes(uow, actor.ws.id, actor.person.id)
    out = [{**row_dict(r["process"]), "stages": [row_dict(s) for s in r["stages"]]} for r in rows]
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
    p = await stages.create_process(uow, actor.ws, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(p)


@router.post(
    "/processes/{ref}/stages", status_code=201, tags=["overlay"], dependencies=[needs("process.admin")]
)
async def add_stage(ref: str, body: StageIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    st = await stages.add_stage(uow, actor.ws, actor.person.id, ref, body.model_dump())
    await uow.commit()
    return row_dict(st)


class StagePatch(BaseModel):
    name: str | None = None
    expected_duration_days: int | None = Field(default=None, ge=0, le=3650)
    wip_limit: int | None = Field(default=None, ge=0, le=10_000)
    requires_approval: bool | None = None
    category: str | None = None
    maps_to_state_category: str | None = None


@router.patch("/processes/{ref}/stages/{stage_key}", tags=["overlay"], dependencies=[needs("process.admin")])
async def patch_stage(
    ref: str, stage_key: str, body: StagePatch, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    """Edit a stage in place (name, expected days, WIP limit, approval, category): the chip popover of UX §3.3."""
    st = await stages.patch_stage(
        uow, actor.ws, actor.person.id, ref, stage_key, body.model_dump(exclude_unset=True)
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
    begin(uow, actor)
    item = await _item(uow, actor.ws.id, id_or_key)
    pl = await stages.move_item(
        uow, actor.ws, actor.person.id, item, stage=body.stage, process=body.process, reason=body.reason
    )
    await uow.commit()
    return row_dict(pl)


@router.get("/items/{id_or_key}/placements", tags=["overlay"])
async def placements(id_or_key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    rows = await stages.placements_of(uow, item)
    return {"data": [{**row_dict(p), "stage": k} for p, k in rows], "next_cursor": None}
