"""Dependency links between items: which link types order work, the cycle check, and creating a link."""

from __future__ import annotations

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import LinkType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.errors import Conflict, Invalid

SUCCESSOR_OF_FROM = {
    LinkType.BLOCKS,
    LinkType.FINISH_TO_START,
    LinkType.START_TO_START,
    LinkType.FINISH_TO_FINISH,
}
SUCCESSOR_OF_TO = {LinkType.DEPENDS_ON, LinkType.IS_BLOCKED_BY}


def edge(link_type: LinkType, a: str, b: str) -> tuple[str, str] | None:
    """(predecessor, successor) for ordering links; None for the informational ones (relates_to, duplicates, clones)."""
    if link_type in SUCCESSOR_OF_FROM:
        return a, b
    if link_type in SUCCESSOR_OF_TO:
        return b, a
    return None


async def would_cycle(uow: UnitOfWork, pred: str, succ: str) -> bool:
    """Whether adding `pred -> succ` would make the ordering links circular (a path `succ -> ... -> pred` exists)."""
    rows = (
        await uow.session.execute(
            select(m.WorkItemLink.from_item_id, m.WorkItemLink.to_item_id, m.WorkItemLink.link_type).where(
                m.WorkItemLink.deleted_at.is_(None)
            )
        )
    ).all()
    nxt: dict[str, set[str]] = {}
    for a, b, t in rows:
        e = edge(t, a, b)
        if e:
            nxt.setdefault(e[0], set()).add(e[1])
    seen, stack = set(), [succ]
    while stack:
        n = stack.pop()
        if n == pred:
            return True
        if n not in seen:
            seen.add(n)
            stack.extend(nxt.get(n, ()))
    return False


async def create_link(
    uow: UnitOfWork, a: m.WorkItem, b: m.WorkItem, link_type: LinkType, lag_days: int | None
) -> m.WorkItemLink:
    """Link `a` to `b`; refuses a self-link, a duplicate and a link that would close a dependency cycle."""
    if a.id == b.id:
        raise Invalid("invalid_link", "an item cannot link to itself", pointer="/to")
    dup = await uow.session.scalar(
        select(m.WorkItemLink.id).where(
            m.WorkItemLink.from_item_id == a.id, m.WorkItemLink.to_item_id == b.id,
            m.WorkItemLink.link_type == link_type, m.WorkItemLink.deleted_at.is_(None),
        )
    )  # fmt: skip
    if dup:
        raise Conflict("duplicate_link", "that link already exists")
    ordered = edge(link_type, a.id, b.id)
    if ordered and await would_cycle(uow, *ordered):
        raise Conflict("dependency_cycle", "that link would make the dependencies circular", pointer="/to")
    link = m.WorkItemLink(
        id=new_id(), from_item_id=a.id, to_item_id=b.id, link_type=link_type, lag_days=lag_days
    )
    uow.session.add(link)
    await uow.session.flush()
    return link
