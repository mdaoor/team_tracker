"""Saved views: create, list, edit, delete/restore and run."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_filter import FilterError
from tt_filter import parse as parse_filter
from tt_schema.base import new_id, utcnow
from tt_schema.enums import ScopeType, TargetType, ViewLayout

from tt_server.acl.visibility import visible
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.query import _bad, _qc
from tt_server.db import models as m
from tt_server.domain.common import emit, row_dict
from tt_server.query import service

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["query"])


# ---------------------------------------------------------------- saved views
class ViewIn(BaseModel):
    name: str = Field(min_length=1)
    filter: dict[str, Any] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list)
    sort_by: list[dict[str, str]] = Field(default_factory=list)
    layout: ViewLayout = ViewLayout.LIST
    weight_scheme_id: str | None = None
    is_shared: bool = False
    scope_type: ScopeType = ScopeType.PERSON
    scope_id: str | None = None


class ViewPatch(BaseModel):
    name: str | None = None
    filter: dict[str, Any] | None = None
    group_by: list[str] | None = None
    sort_by: list[dict[str, str]] | None = None
    layout: ViewLayout | None = None
    weight_scheme_id: str | None = None
    is_shared: bool | None = None


def _validate(filter_: dict[str, Any]) -> None:
    try:
        parse_filter(filter_)
    except FilterError as exc:
        raise ApiError(400, "invalid_filter", exc.message, pointer="/filter" + exc.pointer) from exc


@router.post("/views", status_code=201, dependencies=[needs("view.save")])
async def create_view(body: ViewIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _validate(body.filter)
    if body.is_shared and not actor.acl.can("view.share"):
        raise ApiError(
            403, "forbidden", "missing permission 'view.share'", details={"permission": "view.share"}
        )
    v = m.SavedView(
        id=new_id(),
        workspace_id=actor.ws.id,
        owner_id=actor.person.id,
        entity=TargetType.WORK_ITEM,
        **body.model_dump(),
    )
    uow.session.add(v)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="view.created",
        payload={"after": {"name": v.name, "shared": v.is_shared}},
    )
    await uow.commit()
    return row_dict(v)


@router.get("/views")
async def list_views(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            visible(
                select(m.SavedView).where(
                    m.SavedView.workspace_id == actor.ws.id, m.SavedView.deleted_at.is_(None)
                ),
                actor.acl,
                m.SavedView,
            ).order_by(m.SavedView.name)
        )
    ).all()
    return {
        "data": [dict(row_dict(v), mine=v.owner_id == actor.person.id) for v in rows],
        "next_cursor": None,
    }


async def _view(actor: CurrentActor, uow: Uow, vid: str) -> m.SavedView:
    v: m.SavedView | None = await uow.session.scalar(
        visible(
            select(m.SavedView).where(
                m.SavedView.id == vid,
                m.SavedView.workspace_id == actor.ws.id,
                m.SavedView.deleted_at.is_(None),
            ),
            actor.acl,
            m.SavedView,
        )
    )
    if v is None:
        raise ApiError(404, "not_found", "view not found")
    return v


@router.get("/views/{vid}")
async def get_view(vid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return row_dict(await _view(actor, uow, vid))


@router.patch("/views/{vid}")
async def patch_view(vid: str, body: ViewPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    v = await _view(actor, uow, vid)
    if v.owner_id != actor.person.id:
        raise ApiError(403, "forbidden", "only the owner may change a view")
    data = body.model_dump(exclude_unset=True)
    if "filter" in data:
        _validate(data["filter"])
    if data.get("is_shared") and not actor.acl.can("view.share"):
        raise ApiError(
            403, "forbidden", "missing permission 'view.share'", details={"permission": "view.share"}
        )
    for k, val in data.items():
        setattr(v, k, val)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="view.updated",
        payload={"after": {"id": v.id, "fields": sorted(data)}},
    )
    await uow.commit()
    return row_dict(v)


@router.delete("/views/{vid}", status_code=204)
async def delete_view(vid: str, actor: CurrentActor, uow: Uow) -> Response:
    v = await _view(actor, uow, vid)
    if v.owner_id != actor.person.id and not actor.acl.is_admin:
        raise ApiError(403, "forbidden", "only the owner may delete a view")
    v.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="view.deleted",
        payload={"before": {"id": v.id}},
    )
    await uow.commit()
    return Response(status_code=204)


@router.post("/views/{vid}:restore")
async def restore_view(vid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Undo a view delete (UX 6)."""
    v: m.SavedView | None = await uow.session.scalar(
        select(m.SavedView).where(
            m.SavedView.id == vid,
            m.SavedView.workspace_id == actor.ws.id,
            m.SavedView.deleted_at.is_not(None),
        )
    )
    if v is None or not (v.owner_id == actor.person.id or actor.acl.is_admin):
        raise ApiError(404, "not_found", "view not found")
    v.deleted_at = None
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PERSON,
        entity_id=actor.person.id, event_type="view.restored", payload={"after": {"id": v.id}},
    )  # fmt: skip
    await uow.commit()
    return row_dict(v)


@router.post("/views/{vid}/run")
async def run_view(vid: str, actor: CurrentActor, uow: Uow, cursor: str | None = None) -> dict[str, Any]:
    """Execute a saved view *as the caller*: a shared view never shows more than its opener may see."""
    v = await _view(actor, uow, vid)
    qc = _qc(actor, uow, v.weight_scheme_id)
    try:
        return await service.query_items(
            qc,
            filter=v.filter,
            sort=[{"field": s.get("field", "priority"), "dir": s.get("dir", "desc")} for s in v.sort_by]
            or None,
            group_by=list(v.group_by),
            page={"cursor": cursor},
        )
    except FilterError as exc:
        raise _bad(exc) from exc
