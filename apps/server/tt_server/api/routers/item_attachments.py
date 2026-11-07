"""Item attachments: upload, list, download, delete (plan P1)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import AttachmentKind, TargetType

from tt_server.api.authz import check, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.overlay_support import audit, item_ref_for
from tt_server.api.routers.core import _item
from tt_server.db import models as m
from tt_server.domain.common import row_dict
from tt_server.storage.adapter import StorageError, adapter_from_settings

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["items"])


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
    audit(
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
        check(actor, "item.write_overlay", await item_ref_for(uow, item))
    a.deleted_at = utcnow()
    audit(uow, actor, TargetType.WORK_ITEM, item.id, "attachment.removed", attachment=a.id)
    await uow.commit()
    if a.kind != AttachmentKind.LINK:
        try:
            await adapter_from_settings(request.app.state.settings).delete(a.storage_url)
        except StorageError:
            pass
    return Response(status_code=204)
