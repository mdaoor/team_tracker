"""Authorization helpers: named permissions on routes (`needs`) and inline checks (`check_*`)."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import Depends, Request
from opentelemetry import metrics

from tt_server.acl.context import ObjRef, item_ref, project_ref
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork

log = structlog.get_logger()
DENIALS = metrics.get_meter("tt_server").create_counter(
    "tt_acl_denied", description="Permission denials (visible object, missing permission)"
)


def check(actor: Actor, permission: str, ref: ObjRef | None = None) -> None:
    """403 for a visible object the actor may not touch (invisible objects are 404 earlier, ADR-0008)."""
    if not actor.acl.can(permission, ref):
        log.warning(
            "acl_denied",
            actor=actor.handle,
            permission=permission,
            project_id=ref.project_id if ref else None,
            item_id=ref.item_ids[0] if ref and ref.item_ids else None,
        )
        DENIALS.add(1, {"permission": permission})
        raise ApiError(
            403, "forbidden", f"missing permission {permission!r}", details={"permission": permission}
        )


async def check_item(actor: Actor, uow: UnitOfWork, permission: str, item: m.WorkItem) -> None:
    check(actor, permission, await item_ref(uow.session, item))


async def check_project(actor: Actor, uow: UnitOfWork, permission: str, project: m.Project) -> None:
    check(actor, permission, await project_ref(uow.session, project))


def needs_review() -> Any:
    """Review queue access: `review_queue.resolve` or being someone's line manager (ADR-0006)."""

    async def dep(actor: CurrentActor) -> None:
        if not (actor.acl.can("review_queue.resolve") or actor.acl.managed_ids):
            raise ApiError(
                403,
                "forbidden",
                "missing permission 'review_queue.resolve'",
                details={"permission": "review_queue.resolve"},
            )

    return Depends(dep)


def needs(permission: str, *, item: str | None = None, project: str | None = None) -> Any:
    """Route dependency. `item=` / `project=` name the path parameter that identifies the scoped object."""

    async def dep(request: Request, actor: CurrentActor, uow: Uow) -> None:
        ref: ObjRef | None = None
        if item is not None:
            from tt_server.api.routers.core import _item

            obj = await _item(uow, actor.ws.id, request.path_params[item])
            ref = await item_ref(uow.session, obj)
        elif project is not None:
            from tt_server.api.routers.core import _project

            pobj = await _project(uow, actor.ws.id, request.path_params[project])
            ref = await project_ref(uow.session, pobj)
        check(actor, permission, ref)

    return Depends(dep)
