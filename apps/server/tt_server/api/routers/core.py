"""Workspace-scoped CRUD and read endpoints (tenancy, projects, items, lookups)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from tt_schema import tenancy as schema_tenancy
from tt_schema.enums import (
    Priority,
    StateCategory,
)

from tt_server.api.authz import check_project, needs
from tt_server.api.deps import CurrentActor, Uow, page
from tt_server.api.errors import ApiError
from tt_server.api.params import enum_param
from tt_server.db import models as m
from tt_server.domain.common import row_dict
from tt_server.domain.items import lifecycle, queries
from tt_server.domain.lookup import visible_item, visible_project

router = APIRouter(prefix="/api/v1/w/{ws}")
_ = schema_tenancy


async def _project(uow: Uow, ws_id: str, key: str) -> m.Project:
    return await visible_project(uow, ws_id, key)


async def _project_of(uow: Uow, actor: CurrentActor, project_id: str) -> m.Project:
    """Load a project the actor can see (404 otherwise) from its id."""
    p = await uow.session.get(m.Project, project_id)
    if p is None:
        raise ApiError(404, "not_found", "project not found")
    return await _project(uow, actor.ws.id, p.key)


async def _item(uow: Uow, ws_id: str, id_or_key: str) -> m.WorkItem:
    return await visible_item(uow, ws_id, id_or_key)


# ------------------------------------------------------------------ items


class ItemIn(BaseModel):
    title: str
    project: str
    type_id: str | None = None
    parent: str | None = None
    description: str | None = None
    priority: Priority = Priority.MEDIUM
    assignee_id: str | None = None


@router.post("/items", status_code=201, tags=["items"])
async def create_item(body: ItemIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, body.project)
    await check_project(actor, uow, "item.write_overlay", p)
    parent = await _item(uow, actor.ws.id, body.parent) if body.parent else None
    item = await lifecycle.create_item(
        uow,
        actor.ws,
        actor.person.id,
        p,
        title=body.title,
        assignee_id=body.assignee_id,
        type_id=body.type_id,
        parent=parent,
        description=body.description,
        priority=body.priority,
    )
    await uow.commit()
    return row_dict(item)


@router.get("/items/{id_or_key}", tags=["items"])
async def get_item(id_or_key: str, actor: CurrentActor, uow: Uow, response: Response) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    response.headers["ETag"] = f'"{item.updated_at.isoformat()}"'
    return await queries.item_detail(uow, item)


@router.get("/items", tags=["items"])
async def list_items(
    actor: CurrentActor,
    uow: Uow,
    assignee: str | None = None,
    state_category: str | None = None,
    project: str | None = None,
    team: str | None = None,
    q: str | None = None,
    fields: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    cats = (
        [enum_param(StateCategory, c, "state_category") for c in state_category.split(",")]
        if state_category
        else None
    )
    rows = await queries.list_items(
        uow,
        actor.ws.id,
        actor.acl,
        actor.person.id,
        assignee=assignee,
        state_categories=cats,
        project=project,
        team=team,
        q=q,
        cursor=cursor,
        limit=limit,
    )
    objs = []
    for it, pkey in rows:
        d = row_dict(it)
        d["project_key"] = pkey
        objs.append(type("R", (), {"id": it.id, "d": d}))
    envelope = page(objs, limit)
    data = [o.d for o in envelope["data"]]
    if fields:
        wanted = fields.split(",")
        data = [{k: v for k, v in d.items() if k in wanted} for d in data]
    return {"data": data, "next_cursor": envelope["next_cursor"]}


class StateIn(BaseModel):
    state: str


@router.post(
    "/items/{id_or_key}/state", tags=["items"], dependencies=[needs("item.write_overlay", item="id_or_key")]
)
async def change_state(id_or_key: str, body: StateIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    await lifecycle.change_state(uow, actor.ws, actor.person.id, item, body.state)
    await uow.commit()
    return row_dict(item)


class MoveIn(BaseModel):
    parent: str | None = None


@router.post(
    "/items/{id_or_key}/move", tags=["items"], dependencies=[needs("item.write_overlay", item="id_or_key")]
)
async def move(id_or_key: str, body: MoveIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    parent = await _item(uow, actor.ws.id, body.parent) if body.parent else None
    await lifecycle.reparent(uow, actor.ws, actor.person.id, item, parent)
    await uow.commit()
    return row_dict(item)


@router.get("/tags", tags=["items"])
async def list_tags(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.Tag)
            .where(m.Tag.workspace_id == actor.ws.id, m.Tag.deleted_at.is_(None))
            .order_by(m.Tag.name)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post(
    "/tags/{tag_id}/merge-into/{other_id}", tags=["items"], dependencies=[needs("classification.write")]
)
async def merge_tags(tag_id: str, other_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    dst, moved = await lifecycle.merge_tags(uow, actor.ws, actor.person.id, tag_id, other_id)
    await uow.commit()
    return {"merged_into": dst.id, "links_moved": moved}
