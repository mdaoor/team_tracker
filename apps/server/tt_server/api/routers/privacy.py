"""Personal-data rights and retention (phase 12 §4): export, erase, purge."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain import privacy

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["privacy"])


async def _member(uow: Uow, actor: CurrentActor, ref: str) -> m.WorkspaceMember:
    mem = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == actor.ws.id,
            (m.WorkspaceMember.handle == (ref if ref.startswith("@") else "@" + ref))
            | (m.WorkspaceMember.person_id == ref),
        )
    )
    if mem is None:
        raise ApiError(404, "not_found", "person not found")
    return mem


@router.get("/me/export")
async def export_me(actor: CurrentActor, uow: Uow) -> Response:
    """Everything the workspace holds about the signed-in person, including their private data."""
    data = await privacy.export_person(uow.session, actor.ws, actor.person.id, include_private=True)
    return _download(data, actor.member.handle)


@router.get("/people/{ref}/export", dependencies=[needs("workspace.admin")])
async def export_person(ref: str, actor: CurrentActor, uow: Uow) -> Response:
    mem = await _member(uow, actor, ref)
    data = await privacy.export_person(
        uow.session, actor.ws, mem.person_id, include_private=mem.person_id == actor.person.id
    )
    return _download(data, mem.handle)


def _download(data: dict[str, Any], handle: str) -> Response:
    import json

    return Response(
        json.dumps(data, indent=2, default=str), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="export-{handle.lstrip("@")}.json"'},
    )  # fmt: skip


class EraseIn(BaseModel):
    confirm: str = Field(description="The person's handle, typed again.")


@router.post("/people/{ref}:erase", dependencies=[needs("workspace.admin")])
async def erase(ref: str, body: EraseIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    mem = await _member(uow, actor, ref)
    if body.confirm != mem.handle:
        raise ApiError(
            400, "confirmation_mismatch", "type the person's handle to confirm", pointer="/confirm"
        )
    if mem.person_id == actor.person.id:
        raise ApiError(400, "invalid_target", "you cannot erase yourself; ask another admin")
    counts = await privacy.erase_person(uow, actor.ws, mem.person_id, actor_id=actor.person.id)
    await uow.commit()
    return {"erased": mem.person_id, **counts}


class PurgeIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.post("/retention:purge-raw-input", dependencies=[needs("workspace.admin")])
async def purge(body: PurgeIn, actor: CurrentActor, uow: Uow) -> dict[str, int]:
    n = await privacy.purge_raw_input(uow.session, days=body.days, workspace_id=actor.ws.id)
    await uow.session.commit()
    return {"purged": n}
