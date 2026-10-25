"""Helpers shared by the overlay routers (dimensions, process, products, weights, risks, review, items)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import TargetType

from tt_server.acl.context import item_ref
from tt_server.api.authz import check, check_item, check_project
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.routers.core import _item, _project
from tt_server.db import models as m
from tt_server.domain.errors import NotFound

PREFIX = "/api/v1/w/{ws}"
TARGETS = {"work_item": TargetType.WORK_ITEM, "project": TargetType.PROJECT, "product": TargetType.PRODUCT}


async def item_ref_for(uow: Uow, item: m.WorkItem) -> Any:
    """The ACL object reference of a work item."""
    return await item_ref(uow.session, item)


def begin(uow: Uow, actor: CurrentActor) -> None:
    """Stamp the unit of work with the acting person and a correlation id for the events it emits."""
    uow.context["actor_id"] = actor.person.id
    uow.context.setdefault("correlation_id", new_id())


async def check_target(
    actor: CurrentActor, uow: Uow, permission: str, target_type: TargetType, target_id: str
) -> None:
    """Authorise `permission` on the classification/link target (item and project scopes use their own grants)."""
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


async def target_id_of(uow: Uow, ws_id: str, target_type: TargetType, ref: str) -> str:
    """Resolve an item key / project key / product key (or a raw id) to the target's id; invisible is a 404."""
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
            raise NotFound("not_found", "product not found")
        return p.id
    return ref
