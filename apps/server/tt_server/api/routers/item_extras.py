"""Item companions (plan P1): comments, checklists and dependency links; fields in `item_fields`, files in `item_attachments`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import LinkType, TargetType

from tt_server.api.authz import check, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.overlay_support import audit, item_ref_for
from tt_server.api.routers.core import _item
from tt_server.db import models as m
from tt_server.domain.common import row_dict
from tt_server.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["items"])


# ------------------------------------------------------------------ comments
class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    parent_comment_id: str | None = None


async def _comment(uow: Uow, actor: Actor, cid: str) -> tuple[m.Comment, m.WorkItem]:
    c = await uow.session.scalar(select(m.Comment).where(m.Comment.id == cid, m.Comment.deleted_at.is_(None)))
    if c is None or c.target_type != TargetType.WORK_ITEM:
        raise ApiError(404, "not_found", "comment not found")
    item = await _item(uow, actor.ws.id, c.target_id)  # the comment is as visible as its item
    return c, item


@router.post("/items/{ref}/comments", status_code=201, dependencies=[needs("comment.write", item="ref")])
async def add_comment(ref: str, body: CommentIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    if body.parent_comment_id:
        parent, _ = await _comment(uow, actor, body.parent_comment_id)
        if parent.target_id != item.id:
            raise ApiError(
                400,
                "invalid_parent",
                "the parent comment belongs to another item",
                pointer="/parent_comment_id",
            )
    c = m.Comment(
        id=new_id(), target_type=TargetType.WORK_ITEM, target_id=item.id, author_id=actor.person.id, body=body.body,
        parent_comment_id=body.parent_comment_id,
    )  # fmt: skip
    uow.session.add(c)
    await uow.session.flush()
    audit(uow, actor, TargetType.COMMENT, c.id, "comment.created", item=item.key, length=len(body.body))
    await uow.commit()
    return row_dict(c)


@router.get("/items/{ref}/comments")
async def list_comments(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    rows = await uow.session.scalars(
        select(m.Comment)
        .where(
            m.Comment.target_type == TargetType.WORK_ITEM,
            m.Comment.target_id == item.id,
            m.Comment.deleted_at.is_(None),
        )
        .order_by(m.Comment.created_at)
    )
    return {"data": [row_dict(c) for c in rows.all()], "next_cursor": None}


class CommentPatch(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


@router.patch("/comments/{cid}")
async def edit_comment(cid: str, body: CommentPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    c, _ = await _comment(uow, actor, cid)
    if c.author_id != actor.person.id:
        raise ApiError(403, "forbidden", "only the author may edit a comment")
    c.body, c.edited_at = body.body, utcnow()
    audit(uow, actor, TargetType.COMMENT, c.id, "comment.edited")
    await uow.commit()
    return row_dict(c)


@router.delete("/comments/{cid}", status_code=204)
async def delete_comment(cid: str, actor: CurrentActor, uow: Uow) -> Response:
    c, _ = await _comment(uow, actor, cid)
    if c.author_id != actor.person.id and not actor.acl.is_admin:
        raise ApiError(403, "forbidden", "only the author or an admin may delete a comment")
    c.deleted_at = utcnow()
    audit(uow, actor, TargetType.COMMENT, c.id, "comment.deleted")
    await uow.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ checklists
class ChecklistIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    items: list[str] = Field(default_factory=list, max_length=100)


async def _checklist_dict(uow: Uow, cl: m.Checklist) -> dict[str, Any]:
    rows = await uow.session.scalars(
        select(m.ChecklistItem)
        .where(m.ChecklistItem.checklist_id == cl.id, m.ChecklistItem.deleted_at.is_(None))
        .order_by(m.ChecklistItem.position)
    )
    items = [row_dict(i) for i in rows.all()]
    return {
        **row_dict(cl),
        "items": items,
        "done": sum(1 for i in items if i["is_done"]),
        "total": len(items),
    }


@router.post(
    "/items/{ref}/checklists", status_code=201, dependencies=[needs("item.write_overlay", item="ref")]
)
async def add_checklist(ref: str, body: ChecklistIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    n = len(
        (await uow.session.scalars(select(m.Checklist.id).where(m.Checklist.work_item_id == item.id))).all()
    )
    cl = m.Checklist(id=new_id(), work_item_id=item.id, title=body.title, position=n)
    uow.session.add(cl)
    await uow.session.flush()
    for pos, text in enumerate(body.items):
        uow.session.add(m.ChecklistItem(id=new_id(), checklist_id=cl.id, text=text, position=pos))
    await uow.session.flush()
    audit(
        uow,
        actor,
        TargetType.WORK_ITEM,
        item.id,
        "checklist.created",
        checklist=cl.id,
        items=len(body.items),
    )
    await uow.commit()
    return await _checklist_dict(uow, cl)


@router.get("/items/{ref}/checklists")
async def list_checklists(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    rows = await uow.session.scalars(
        select(m.Checklist)
        .where(m.Checklist.work_item_id == item.id, m.Checklist.deleted_at.is_(None))
        .order_by(m.Checklist.position)
    )
    return {"data": [await _checklist_dict(uow, c) for c in rows.all()], "next_cursor": None}


class ChecklistItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


async def _cl_item(uow: Uow, actor: Actor, iid: str) -> tuple[m.ChecklistItem, m.Checklist, m.WorkItem]:
    ci = await uow.session.scalar(
        select(m.ChecklistItem).where(m.ChecklistItem.id == iid, m.ChecklistItem.deleted_at.is_(None))
    )
    cl = await uow.session.get(m.Checklist, ci.checklist_id) if ci else None
    if ci is None or cl is None or cl.deleted_at is not None:
        raise ApiError(404, "not_found", "checklist item not found")
    item = await _item(uow, actor.ws.id, cl.work_item_id)
    check(actor, "item.write_overlay", await item_ref_for(uow, item))
    return ci, cl, item


@router.post("/checklists/{cid}/items", status_code=201)
async def add_checklist_item(
    cid: str, body: ChecklistItemIn, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    cl = await uow.session.scalar(
        select(m.Checklist).where(m.Checklist.id == cid, m.Checklist.deleted_at.is_(None))
    )
    if cl is None:
        raise ApiError(404, "not_found", "checklist not found")
    item = await _item(uow, actor.ws.id, cl.work_item_id)
    check(actor, "item.write_overlay", await item_ref_for(uow, item))
    n = len(
        (
            await uow.session.scalars(select(m.ChecklistItem.id).where(m.ChecklistItem.checklist_id == cl.id))
        ).all()
    )
    ci = m.ChecklistItem(id=new_id(), checklist_id=cl.id, text=body.text, position=n)
    uow.session.add(ci)
    await uow.session.flush()
    audit(uow, actor, TargetType.WORK_ITEM, item.id, "checklist.item_changed", checklist=cl.id, added=ci.id)
    await uow.commit()
    return row_dict(ci)


class ChecklistItemPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    is_done: bool | None = None


@router.patch("/checklist-items/{iid}")
async def patch_checklist_item(
    iid: str, body: ChecklistItemPatch, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    ci, cl, item = await _cl_item(uow, actor, iid)
    if body.text is not None:
        ci.text = body.text
    if body.is_done is not None and body.is_done != ci.is_done:
        ci.is_done = body.is_done
        ci.done_by, ci.done_at = (actor.person.id, utcnow()) if body.is_done else (None, None)
    audit(
        uow,
        actor,
        TargetType.WORK_ITEM,
        item.id,
        "checklist.item_changed",
        checklist=cl.id,
        changed=ci.id,
        is_done=ci.is_done,
    )
    await uow.commit()
    return row_dict(ci)


@router.delete("/checklist-items/{iid}", status_code=204)
async def delete_checklist_item(iid: str, actor: CurrentActor, uow: Uow) -> Response:
    ci, cl, item = await _cl_item(uow, actor, iid)
    ci.deleted_at = utcnow()
    audit(uow, actor, TargetType.WORK_ITEM, item.id, "checklist.item_changed", checklist=cl.id, removed=ci.id)
    await uow.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ links
SUCCESSOR_OF_FROM = {
    LinkType.BLOCKS,
    LinkType.FINISH_TO_START,
    LinkType.START_TO_START,
    LinkType.FINISH_TO_FINISH,
}
SUCCESSOR_OF_TO = {LinkType.DEPENDS_ON, LinkType.IS_BLOCKED_BY}


def _edge(link_type: LinkType, a: str, b: str) -> tuple[str, str] | None:
    """(predecessor, successor) for ordering links; None for the informational ones (relates_to, duplicates, clones)."""
    if link_type in SUCCESSOR_OF_FROM:
        return a, b
    if link_type in SUCCESSOR_OF_TO:
        return b, a
    return None


class LinkIn(BaseModel):
    to: str
    link_type: LinkType = LinkType.RELATES_TO
    lag_days: int | None = Field(default=None, ge=-365, le=365)


async def _would_cycle(uow: Uow, pred: str, succ: str) -> bool:
    rows = (await uow.session.execute(
        select(m.WorkItemLink.from_item_id, m.WorkItemLink.to_item_id, m.WorkItemLink.link_type).where(m.WorkItemLink.deleted_at.is_(None))
    )).all()  # fmt: skip
    nxt: dict[str, set[str]] = {}
    for a, b, t in rows:
        e = _edge(t, a, b)
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


@router.post("/items/{ref}/links", status_code=201, dependencies=[needs("item.write_overlay", item="ref")])
async def add_link(ref: str, body: LinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    a = await _item(uow, actor.ws.id, ref)
    b = await _item(uow, actor.ws.id, body.to)  # an invisible target is a 404, like any other lookup
    if a.id == b.id:
        raise ApiError(400, "invalid_link", "an item cannot link to itself", pointer="/to")
    dup = await uow.session.scalar(
        select(m.WorkItemLink.id).where(
            m.WorkItemLink.from_item_id == a.id, m.WorkItemLink.to_item_id == b.id,
            m.WorkItemLink.link_type == body.link_type, m.WorkItemLink.deleted_at.is_(None),
        )
    )  # fmt: skip
    if dup:
        raise ApiError(409, "duplicate_link", "that link already exists")
    edge = _edge(body.link_type, a.id, b.id)
    if edge and await _would_cycle(uow, *edge):
        raise ApiError(
            409, "dependency_cycle", "that link would make the dependencies circular", pointer="/to"
        )
    link = m.WorkItemLink(
        id=new_id(), from_item_id=a.id, to_item_id=b.id, link_type=body.link_type, lag_days=body.lag_days
    )
    uow.session.add(link)
    await uow.session.flush()
    audit(
        uow,
        actor,
        TargetType.WORK_ITEM,
        a.id,
        "item.linked",
        to=b.key,
        link_type=body.link_type.value,
        link=link.id,
    )
    await uow.commit()
    return {**row_dict(link), "from": a.key, "to": b.key}


@router.get("/items/{ref}/links")
async def list_links(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    rows = (await uow.session.scalars(
        select(m.WorkItemLink).where(
            (m.WorkItemLink.from_item_id == item.id) | (m.WorkItemLink.to_item_id == item.id), m.WorkItemLink.deleted_at.is_(None)
        )
    )).all()  # fmt: skip
    out = []
    for link in rows:
        other_id = link.to_item_id if link.from_item_id == item.id else link.from_item_id
        try:
            other = await _item(
                uow, actor.ws.id, other_id
            )  # a linked item the viewer cannot see is left out, not named
        except DomainError:
            continue
        out.append(
            {
                **row_dict(link),
                "direction": "out" if link.from_item_id == item.id else "in",
                "other": other.key,
            }
        )
    return {"data": out, "next_cursor": None}


@router.delete("/links/{lid}", status_code=204)
async def delete_link(lid: str, actor: CurrentActor, uow: Uow) -> Response:
    link = await uow.session.scalar(
        select(m.WorkItemLink).where(m.WorkItemLink.id == lid, m.WorkItemLink.deleted_at.is_(None))
    )
    if link is None:
        raise ApiError(404, "not_found", "link not found")
    a = await _item(uow, actor.ws.id, link.from_item_id)
    check(actor, "item.write_overlay", await item_ref_for(uow, a))
    link.deleted_at = utcnow()
    audit(uow, actor, TargetType.WORK_ITEM, a.id, "item.unlinked", link=link.id)
    await uow.commit()
    return Response(status_code=204)
