"""Helpers shared by the op modules: handle and key lookups, the result wrapper, the item-level permission check."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tt_server.api.commands.registry import Ctx, Outcome
from tt_server.api.routers import core
from tt_server.db import models as m
from tt_server.domain.errors import NotFound


def _res(out: Any) -> Outcome:
    return Outcome(result=out if isinstance(out, dict) else {"value": out})


async def _person(ctx: Ctx, handle: str) -> str:
    h = handle.lstrip("@")
    pid = await ctx.uow.session.scalar(
        select(m.WorkspaceMember.person_id).where(
            m.WorkspaceMember.workspace_id == ctx.actor.ws.id,
            m.WorkspaceMember.handle.in_([h, "@" + h]) | (m.WorkspaceMember.person_id == h),
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )
    if pid is None:
        raise NotFound("not_found", f"unknown person {handle!r}")
    return str(pid)


async def _team(ctx: Ctx, ref: str) -> m.Team:
    t = await ctx.uow.session.scalar(
        select(m.Team).where(
            m.Team.workspace_id == ctx.actor.ws.id,
            (m.Team.slug == ref) | (m.Team.id == ref),
            m.Team.deleted_at.is_(None),
        )
    )
    if t is None:
        raise NotFound("not_found", f"unknown team {ref!r}")
    return t


async def _items(ctx: Ctx, keys: list[str]) -> list[m.WorkItem]:
    return [await core._item(ctx.uow, ctx.actor.ws.id, k) for k in dict.fromkeys(keys)]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "team"


async def _need_item(ctx: Ctx, permission: str, item: m.WorkItem) -> None:
    from tt_server.api.authz import check_item

    await check_item(ctx.actor, ctx.uow, permission, item)
