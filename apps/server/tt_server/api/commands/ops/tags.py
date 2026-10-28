"""Tags, dimension values and classification. (Phase 14)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from tt_schema.enums import (
    TargetType,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    core,
    overlay,
)
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.errors import NotFound
from tt_server.domain.items import service as items_svc

# ------------------------------------------------------------------ dimensions & tags


class TagMerge(BaseModel):
    tag: str
    into: str


@command(
    "tag.merge", "tags", "Merge one tag into another", TagMerge, token_scope="admin", permission="classification.write", destructive=True,
    example_args={"tag": "q-4", "into": "q4"}, example_text="merge tag q-4 into q4",
)  # fmt: skip
async def tag_merge(ctx: Ctx, a: TagMerge) -> Outcome:
    ids = []
    for name in (a.tag, a.into):
        t = await ctx.uow.session.scalar(select(m.Tag).where(m.Tag.workspace_id == ctx.actor.ws.id, (func.lower(m.Tag.name) == name.lstrip("#").lower()) | (m.Tag.id == name), m.Tag.deleted_at.is_(None)))  # fmt: skip
        if t is None:
            raise NotFound("not_found", f"unknown tag {name!r}")
        ids.append(t.id)
    return _res(await core.merge_tags(ids[0], ids[1], ctx.actor, ctx.uow))


class TagCreate(BaseModel):
    name: str


@command(
    "tag.create", "tags", "Create a tag", TagCreate, permission="classification.write", modes=("manager", "pm", "admin", "scrum_master"),
    example_args={"name": "q4"}, example_text="new tag q4",
)  # fmt: skip
async def tag_create(ctx: Ctx, a: TagCreate) -> Outcome:
    tag, created = await items_svc.get_or_create_tag(
        ctx.uow, ctx.actor.ws, a.name.lstrip("#"), provisional=False
    )
    if created:
        emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.PROJECT, entity_id=ctx.actor.ws.id, event_type="tag.created", payload={"after": {"name": tag.name}})  # fmt: skip
    return Outcome(result={"id": tag.id, "name": tag.name, "created": created})


class DimensionValueAdd(BaseModel):
    dimension: str
    name: str
    parent: str | None = None


@command(
    "dimension.value.add", "tags", "Add a value to a dimension (customer, theme, ...)", DimensionValueAdd, token_scope="admin", permission="dimension.admin",
    example_args={"dimension": "theme", "name": "Checkout"}, example_text="add Checkout to theme",
)  # fmt: skip
async def dimension_value_add(ctx: Ctx, a: DimensionValueAdd) -> Outcome:
    return _res(
        await overlay.create_value(
            a.dimension,
            overlay.ValueIn(name=a.name, key=_slug(a.name), parent_id=a.parent),
            ctx.actor,
            ctx.uow,
        )
    )


class Classify(BaseModel):
    target: str = Field(description="item or project key")
    dimension: str
    value: str
    target_type: Literal["work_item", "project", "product"] = "work_item"


@command(
    "classify", "tags", "Classify an item or project on a dimension", Classify, permission="classification.write", scope="item", scope_arg="target",
    example_args={"target": "WEB-101", "dimension": "theme", "value": "checkout"}, example_text="classify WEB-101 as theme checkout",
)  # fmt: skip
async def classify(ctx: Ctx, a: Classify) -> Outcome:
    body = overlay.ClassifyIn(
        target_type=TargetType(a.target_type), target_id=a.target, dimension=a.dimension, value=a.value
    )
    return _res(await overlay.classify(body, ctx.actor, ctx.uow))
