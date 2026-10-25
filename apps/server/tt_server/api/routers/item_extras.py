"""Item companions (plan P1): comments, checklists, links, custom fields, attachments."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import AttachmentKind, FieldType, LinkType, TargetType

from tt_server.api.authz import check, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _item, _project, row_dict
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.storage.adapter import StorageError, adapter_from_settings

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["items"])


def _ev(uow: Uow, actor: Actor, etype: TargetType, eid: str, event: str, **after: Any) -> None:
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=etype, entity_id=eid, event_type=event,
        payload={"after": after},
    )  # fmt: skip


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
    _ev(uow, actor, TargetType.COMMENT, c.id, "comment.created", item=item.key, length=len(body.body))
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
    _ev(uow, actor, TargetType.COMMENT, c.id, "comment.edited")
    await uow.commit()
    return row_dict(c)


@router.delete("/comments/{cid}", status_code=204)
async def delete_comment(cid: str, actor: CurrentActor, uow: Uow) -> Response:
    c, _ = await _comment(uow, actor, cid)
    if c.author_id != actor.person.id and not actor.acl.is_admin:
        raise ApiError(403, "forbidden", "only the author or an admin may delete a comment")
    c.deleted_at = utcnow()
    _ev(uow, actor, TargetType.COMMENT, c.id, "comment.deleted")
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
    _ev(
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
    check(actor, "item.write_overlay", await _ref(uow, item))
    return ci, cl, item


async def _ref(uow: Uow, item: m.WorkItem) -> Any:
    from tt_server.acl.context import item_ref

    return await item_ref(uow.session, item)


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
    check(actor, "item.write_overlay", await _ref(uow, item))
    n = len(
        (
            await uow.session.scalars(select(m.ChecklistItem.id).where(m.ChecklistItem.checklist_id == cl.id))
        ).all()
    )
    ci = m.ChecklistItem(id=new_id(), checklist_id=cl.id, text=body.text, position=n)
    uow.session.add(ci)
    await uow.session.flush()
    _ev(uow, actor, TargetType.WORK_ITEM, item.id, "checklist.item_changed", checklist=cl.id, added=ci.id)
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
    _ev(
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
    _ev(uow, actor, TargetType.WORK_ITEM, item.id, "checklist.item_changed", checklist=cl.id, removed=ci.id)
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
    _ev(
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
        except ApiError:
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
    check(actor, "item.write_overlay", await _ref(uow, a))
    link.deleted_at = utcnow()
    _ev(uow, actor, TargetType.WORK_ITEM, a.id, "item.unlinked", link=link.id)
    await uow.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ custom fields
KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    key: str = Field(pattern=KEY.pattern)
    field_type: FieldType
    project: str | None = None
    options: list[str] | None = None
    applies_to_types: list[str] | None = None
    is_required: bool = False


def _need_options(t: FieldType, options: list[str] | None) -> None:
    if t in (FieldType.SELECT, FieldType.MULTI_SELECT) and not options:
        raise ApiError(400, "invalid_field", "select fields need `options`", pointer="/options")


@router.post("/custom-fields", status_code=201, dependencies=[needs("workspace.admin")])
async def create_field(body: FieldIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _need_options(body.field_type, body.options)
    project_id = (await _project(uow, actor.ws.id, body.project)).id if body.project else None
    dup = await uow.session.scalar(
        select(m.CustomFieldDefinition.id).where(
            m.CustomFieldDefinition.workspace_id == actor.ws.id, m.CustomFieldDefinition.key == body.key,
            m.CustomFieldDefinition.project_id.is_(None) if project_id is None else m.CustomFieldDefinition.project_id == project_id,
            m.CustomFieldDefinition.deleted_at.is_(None),
        )
    )  # fmt: skip
    if dup:
        raise ApiError(
            409, "duplicate_field", f"a field with key {body.key!r} already exists here", pointer="/key"
        )
    f = m.CustomFieldDefinition(
        id=new_id(), workspace_id=actor.ws.id, name=body.name, key=body.key, field_type=body.field_type, project_id=project_id,
        options=body.options, applies_to_types=body.applies_to_types, is_required=body.is_required,
    )  # fmt: skip
    uow.session.add(f)
    await uow.session.flush()
    _ev(
        uow,
        actor,
        TargetType.PROJECT,
        project_id or actor.ws.id,
        "custom_field.created",
        key=f.key,
        field_type=f.field_type.value,
    )
    await uow.commit()
    return row_dict(f)


@router.get("/custom-fields")
async def list_fields(actor: CurrentActor, uow: Uow, project: str | None = None) -> dict[str, Any]:
    q = select(m.CustomFieldDefinition).where(
        m.CustomFieldDefinition.workspace_id == actor.ws.id, m.CustomFieldDefinition.deleted_at.is_(None)
    )
    if project:
        p = await _project(uow, actor.ws.id, project)
        q = q.where(
            (m.CustomFieldDefinition.project_id.is_(None)) | (m.CustomFieldDefinition.project_id == p.id)
        )
    return {
        "data": [
            row_dict(f) for f in (await uow.session.scalars(q.order_by(m.CustomFieldDefinition.key))).all()
        ],
        "next_cursor": None,
    }


class FieldPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    options: list[str] | None = None
    is_required: bool | None = None


@router.patch("/custom-fields/{fid}", dependencies=[needs("workspace.admin")])
async def patch_field(fid: str, body: FieldPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    f = await _field(uow, actor, fid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    _need_options(f.field_type, f.options)
    _ev(uow, actor, TargetType.PROJECT, f.project_id or actor.ws.id, "custom_field.updated", key=f.key)
    await uow.commit()
    return row_dict(f)


@router.delete("/custom-fields/{fid}", status_code=204, dependencies=[needs("workspace.admin")])
async def delete_field(fid: str, actor: CurrentActor, uow: Uow) -> Response:
    f = await _field(uow, actor, fid)
    f.deleted_at = utcnow()
    _ev(uow, actor, TargetType.PROJECT, f.project_id or actor.ws.id, "custom_field.deleted", key=f.key)
    await uow.commit()
    return Response(status_code=204)


async def _field(uow: Uow, actor: Actor, fid: str) -> m.CustomFieldDefinition:
    f = await uow.session.scalar(
        select(m.CustomFieldDefinition).where(
            m.CustomFieldDefinition.id == fid, m.CustomFieldDefinition.workspace_id == actor.ws.id, m.CustomFieldDefinition.deleted_at.is_(None)
        )
    )  # fmt: skip
    if f is None:
        raise ApiError(404, "not_found", "custom field not found")
    return f


class ValueIn(BaseModel):
    value: Any = None


def _coerce(f: m.CustomFieldDefinition, v: Any) -> dict[str, Any]:
    """Validate against the definition; returns the typed columns to set."""
    empty: dict[str, Any] = {"value_text": None, "value_number": None, "value_date": None, "value_json": None}
    if v is None:
        return empty
    t = f.field_type
    try:
        if t in (FieldType.TEXT, FieldType.URL, FieldType.PERSON):
            if not isinstance(v, str):
                raise ValueError("expected text")
            if t == FieldType.URL and not re.match(r"^https?://", v):
                raise ValueError("expected an http(s) URL")
            return {**empty, "value_text": v}
        if t == FieldType.NUMBER:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("expected a number")
            return {**empty, "value_number": v}
        if t == FieldType.DATE:
            return {**empty, "value_date": date.fromisoformat(str(v))}
        if t == FieldType.BOOL:
            if not isinstance(v, bool):
                raise ValueError("expected true or false")
            return {**empty, "value_json": v}
        opts = set(f.options or [])
        if t == FieldType.SELECT:
            if v not in opts:
                raise ValueError(f"must be one of {sorted(opts)}")
            return {**empty, "value_text": v}
        if not isinstance(v, list) or not set(v) <= opts:
            raise ValueError(f"expected a list drawn from {sorted(opts)}")
        return {**empty, "value_json": v}
    except ValueError as exc:
        raise ApiError(400, "invalid_value", f"{f.key}: {exc}", pointer="/value") from exc


@router.put("/items/{ref}/custom-fields/{key}", dependencies=[needs("item.write_overlay", item="ref")])
async def set_value(ref: str, key: str, body: ValueIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    f = await uow.session.scalar(
        select(m.CustomFieldDefinition)
        .where(
            m.CustomFieldDefinition.workspace_id == actor.ws.id,
            m.CustomFieldDefinition.key == key,
            m.CustomFieldDefinition.deleted_at.is_(None),
            (m.CustomFieldDefinition.project_id.is_(None))
            | (m.CustomFieldDefinition.project_id == item.project_id),
        )
        .order_by(m.CustomFieldDefinition.project_id.is_(None))
    )  # fmt: skip  (a project-level field shadows a workspace-level one)
    if f is None:
        raise ApiError(404, "not_found", f"no custom field {key!r}")
    if f.applies_to_types and item.type_id not in f.applies_to_types:
        raise ApiError(400, "invalid_value", f"{key} does not apply to this item type")
    if body.value is None and f.is_required:
        raise ApiError(400, "invalid_value", f"{key} is required", pointer="/value")
    cols = _coerce(f, body.value)
    row = await uow.session.scalar(
        select(m.CustomFieldValue).where(
            m.CustomFieldValue.work_item_id == item.id, m.CustomFieldValue.field_definition_id == f.id
        )
    )
    if row is None:
        row = m.CustomFieldValue(id=new_id(), work_item_id=item.id, field_definition_id=f.id)
        uow.session.add(row)
    for k, v in cols.items():
        setattr(row, k, v)
    await uow.session.flush()
    _ev(uow, actor, TargetType.WORK_ITEM, item.id, "item.custom_field_set", key=key)
    await uow.commit()
    return {"key": key, "value": body.value}


@router.get("/items/{ref}/custom-fields")
async def get_values(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    rows = (await uow.session.execute(
        select(m.CustomFieldDefinition, m.CustomFieldValue)
        .join(m.CustomFieldValue, m.CustomFieldValue.field_definition_id == m.CustomFieldDefinition.id)
        .where(m.CustomFieldValue.work_item_id == item.id, m.CustomFieldDefinition.deleted_at.is_(None))
    )).all()  # fmt: skip
    out: dict[str, Any] = {}
    for f, v in rows:
        if v.value_text is None and v.value_number is None and v.value_date is None and v.value_json is None:
            continue  # a cleared value
        out[f.key] = (
            v.value_text if v.value_text is not None
            else float(v.value_number) if v.value_number is not None
            else v.value_date.isoformat() if v.value_date is not None
            else v.value_json
        )  # fmt: skip
    return {"data": out}


# ------------------------------------------------------------------ attachments
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")
INLINE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"}


class LinkAttachmentIn(BaseModel):
    url: str = Field(pattern=r"^https?://")
    title: str | None = None


@router.post("/items/{ref}/attachments", status_code=201, dependencies=[needs("comment.write", item="ref")])
async def upload(
    ref: str,
    request: Request,
    actor: CurrentActor,
    uow: Uow,
    filename: str,
    kind: AttachmentKind = AttachmentKind.FILE,
) -> dict[str, Any]:
    """Raw bytes in the body (`?filename=`), or `kind=link` with a JSON `{url}` body."""
    item = await _item(uow, actor.ws.id, ref)
    settings = request.app.state.settings
    data = await request.body()
    name = SAFE_NAME.sub("_", filename.replace("\\", "/").rsplit("/", 1)[-1])[:200] or "file"
    att_id = new_id()
    if kind == AttachmentKind.LINK:
        try:
            link = LinkAttachmentIn.model_validate_json(data)
        except ValidationError as exc:
            raise ApiError(
                400, "invalid_link", "a link attachment needs a JSON body {url: http(s)://…}", pointer="/url"
            ) from exc
        storage_url, mime, size = link.url, "text/uri-list", 0
        name = link.title or link.url
    else:
        if not data:
            raise ApiError(400, "empty_upload", "the upload is empty")
        if len(data) > settings.attachment_max_bytes:
            raise ApiError(413, "too_large", f"uploads are limited to {settings.attachment_max_bytes} bytes")
        mime = request.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
        storage_url = await adapter_from_settings(settings).put(f"{actor.ws.id}/{item.id}/{att_id}", data)
        size = len(data)
    a = m.Attachment(
        id=att_id, target_type=TargetType.WORK_ITEM, target_id=item.id, uploader_id=actor.person.id, filename=name,
        mime_type=mime, size_bytes=size, storage_url=storage_url, kind=kind,
    )  # fmt: skip
    uow.session.add(a)
    await uow.session.flush()
    _ev(
        uow,
        actor,
        TargetType.WORK_ITEM,
        item.id,
        "attachment.added",
        attachment=a.id,
        filename=name,
        size=size,
    )
    await uow.commit()
    return row_dict(a, "storage_url")


@router.get("/items/{ref}/attachments")
async def list_attachments(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    rows = await uow.session.scalars(
        select(m.Attachment)
        .where(m.Attachment.target_id == item.id, m.Attachment.deleted_at.is_(None))
        .order_by(m.Attachment.created_at)
    )
    return {"data": [row_dict(a, "storage_url") for a in rows.all()], "next_cursor": None}


async def _attachment(uow: Uow, actor: Actor, aid: str) -> tuple[m.Attachment, m.WorkItem]:
    a = await uow.session.scalar(
        select(m.Attachment).where(m.Attachment.id == aid, m.Attachment.deleted_at.is_(None))
    )
    if a is None or a.target_type != TargetType.WORK_ITEM:
        raise ApiError(404, "not_found", "attachment not found")
    return a, await _item(uow, actor.ws.id, a.target_id)


@router.get("/attachments/{aid}/content")
async def download(aid: str, request: Request, actor: CurrentActor, uow: Uow) -> Response:
    a, _ = await _attachment(uow, actor, aid)
    if a.kind == AttachmentKind.LINK:
        return Response(status_code=307, headers={"Location": a.storage_url})
    store = adapter_from_settings(request.app.state.settings)
    if hasattr(
        store, "signed_download_url"
    ):  # object storage: hand the client a short-lived URL instead of proxying the bytes
        return Response(
            status_code=307,
            headers={"Location": store.signed_download_url(a.storage_url), "Cache-Control": "no-store"},
        )
    try:
        data = await store.get(a.storage_url)
    except StorageError as exc:
        raise ApiError(404, "blob_missing", "the file is no longer in storage") from exc
    inline = a.mime_type in INLINE_MIME
    disp = "inline" if inline else "attachment"
    return Response(
        data, media_type=a.mime_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f'{disp}; filename="{a.filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )  # fmt: skip


@router.delete("/attachments/{aid}", status_code=204)
async def delete_attachment(aid: str, request: Request, actor: CurrentActor, uow: Uow) -> Response:
    a, item = await _attachment(uow, actor, aid)
    if a.uploader_id != actor.person.id:
        check(actor, "item.write_overlay", await _ref(uow, item))
    a.deleted_at = utcnow()
    _ev(uow, actor, TargetType.WORK_ITEM, item.id, "attachment.removed", attachment=a.id)
    await uow.commit()
    if a.kind != AttachmentKind.LINK:
        try:
            await adapter_from_settings(request.app.state.settings).delete(a.storage_url)
        except StorageError:
            pass
    return Response(status_code=204)
