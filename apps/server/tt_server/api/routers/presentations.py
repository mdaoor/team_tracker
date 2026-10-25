"""Phase 7 API: snapshot, presentations (+ slides, templates, data, render, share), insights, daily-log summary."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from tt_server.api.authz import check, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.csp import add_nonce, html_csp, new_nonce
from tt_server.db import models as m
from tt_server.domain import deckdata
from tt_server.domain import presentation_service as svc
from tt_server.domain import presentations as pres
from tt_server.domain.snapshot import json_schema, parse_scope
from tt_server.exports import presentation as exp

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["presentation"])


def _json(body: Any) -> Response:
    """Straight `json.dumps`: FastAPI's `jsonable_encoder` walk costs about as much as the whole assembly on a 50 k-item snapshot."""
    return Response(json.dumps(body, default=str, separators=(",", ":")), media_type="application/json")


public = APIRouter(tags=["presentation"])
shell = APIRouter(tags=["presentation"])

BUNDLE = Path(__file__).resolve().parents[4] / "presentation" / "dist" / "presentation.html"


def _ev(uow: Uow, actor: Actor, p: m.Presentation, event: str, **payload: Any) -> None:
    svc.announce(uow, actor.ws.id, actor.person.id, p, event, **payload)


# ---------------------------------------------------------------- snapshot
@router.get("/snapshot")
async def snapshot(
    actor: CurrentActor, uow: Uow, scope: str = "workspace", days: int = 14, weight_scheme: str | None = None,
    fresh: bool = False, as_of: datetime | None = None,
) -> Response:  # fmt: skip
    if not 1 <= days <= 366:
        raise ApiError(400, "invalid_range", "days must be 1..366", pointer="/days")
    parse_scope(scope)
    scheme = await deckdata.scheme_by_ref(uow.session, actor.ws.id, weight_scheme)
    qc = deckdata.qctx(
        uow.session, actor.ws, actor.person, actor.member, actor.acl, scheme.id if scheme else None
    )
    if as_of is not None:
        qc.now = as_of
    return _json(
        await deckdata.get_snapshot(
            qc,
            actor.ws,
            scope=scope,
            days=days,
            scheme=scheme,
            viewer=actor.handle,
            fresh=fresh or as_of is not None,
            cache=as_of is None,
        )
    )


@router.get("/meta/schema/snapshot")
async def snapshot_schema() -> dict[str, Any]:
    return json_schema()


# ---------------------------------------------------------------- presentations
class PresentationIn(BaseModel):
    title: str = Field(min_length=1)
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    weight_scheme_id: str | None = None
    theme: str = "auto"
    slides: list[pres.SlideIn] = Field(default_factory=list)
    is_shared: bool = False


class PresentationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    scope: str | None = None
    range_days: int | None = Field(default=None, ge=1, le=366)
    weight_scheme_id: str | None = None
    theme: str | None = None
    slides: list[pres.SlideIn] | None = None
    is_shared: bool | None = None


async def _get(actor: Actor, uow: Uow, pid: str, *, write: bool = False) -> m.Presentation:
    return await svc.get_visible(uow, actor.ws.id, actor.person.id, actor.acl.is_admin, pid, write=write)


def _check_shared(actor: Actor, shared: bool) -> None:
    if shared:
        check(actor, "view.share")


async def _check_refs(uow: Uow, actor: Actor, scope: str, scheme_id: str | None) -> None:
    await svc.check_refs(uow, actor.ws.id, scope, scheme_id)


@router.get("/presentations/templates")
async def templates(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """The gallery as the builder's "Start from template" list (the full CRUD lives under /presentation-templates)."""
    return {
        "data": await svc.template_gallery(uow, actor.ws.id, actor.person.id, actor.acl.is_admin),
        "next_cursor": None,
    }


@router.post("/presentations", status_code=201, dependencies=[needs("view.save")])
async def create(body: PresentationIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _check_shared(actor, body.is_shared)
    p = await svc.create(
        uow,
        actor.ws.id,
        actor.person.id,
        title=body.title,
        scope=body.scope,
        range_days=body.range_days,
        weight_scheme_id=body.weight_scheme_id,
        theme=body.theme,
        slides=body.slides,
        is_shared=body.is_shared,
    )
    await uow.commit()
    return deckdata.presentation_dict(p)


class FromTemplate(BaseModel):
    template: str
    title: str | None = None
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    weight_scheme_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/presentations:from-template", status_code=201, dependencies=[needs("view.save")])
async def from_template(body: FromTemplate, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Copy a template into a new deck. The deck keeps `template_id`/`template_version` as provenance only (copy semantics)."""

    p = await svc.create_from_template(
        uow,
        actor.ws,
        actor.person,
        actor.member,
        actor.acl,
        template=body.template,
        title=body.title,
        scope=body.scope,
        range_days=body.range_days,
        weight_scheme_id=body.weight_scheme_id,
        params=body.params,
    )
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.get("/presentations")
async def list_presentations(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    data = await svc.list_visible(uow, actor.ws.id, actor.person.id, actor.acl.is_admin)
    return {"data": data, "next_cursor": None}


@router.get("/presentations/{pid}")
async def get_presentation(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return deckdata.presentation_dict(await _get(actor, uow, pid))


@router.patch("/presentations/{pid}")
async def patch_presentation(
    pid: str, body: PresentationPatch, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    data = body.model_dump(exclude_unset=True)
    if data.get("is_shared"):
        _check_shared(actor, True)
    await svc.update(uow, actor.ws.id, actor.person.id, p, data, body.slides)
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.delete("/presentations/{pid}", status_code=204)
async def delete_presentation(pid: str, actor: CurrentActor, uow: Uow) -> Response:
    p = await _get(actor, uow, pid, write=True)
    svc.delete(uow, actor.ws.id, actor.person.id, p)
    await uow.commit()
    return Response(status_code=204)


@router.post("/presentations/{pid}:restore")
async def restore_presentation(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Undo a delete (UX 6). The share link stays revoked: re-share explicitly."""
    p = await svc.restore(uow, actor.ws.id, actor.person.id, actor.acl.is_admin, pid)
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.post("/presentations/{pid}:duplicate", status_code=201, dependencies=[needs("view.save")])
async def duplicate(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    src = await _get(actor, uow, pid)
    p = await svc.duplicate(uow, actor.ws.id, actor.person.id, src)
    await uow.commit()
    return deckdata.presentation_dict(p)


class SlideAdd(BaseModel):
    slide: pres.SlideIn
    position: int | None = None


@router.post("/presentations/{pid}/slides", status_code=201)
async def add_slide(pid: str, body: SlideAdd, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    svc.add_slide(uow, actor.ws.id, actor.person.id, p, body.slide, body.position)
    await uow.commit()
    return deckdata.presentation_dict(p)


class SlidePatch(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    config: dict[str, Any] | None = None
    notes: str | None = None
    layout: str | None = None
    live: bool | None = None
    fit: Literal["paginate", "shrink"] | None = None


@router.patch("/presentations/{pid}/slides/{sid}")
async def patch_slide(pid: str, sid: str, body: SlidePatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    svc.patch_slide(uow, actor.ws.id, actor.person.id, p, sid, body.model_dump(exclude_unset=True))
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.delete("/presentations/{pid}/slides/{sid}")
async def delete_slide(pid: str, sid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    svc.delete_slide(uow, actor.ws.id, actor.person.id, p, sid)
    await uow.commit()
    return deckdata.presentation_dict(p)


class Reorder(BaseModel):
    ids: list[str]


@router.put("/presentations/{pid}/slides:reorder")
async def reorder(pid: str, body: Reorder, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    svc.reorder_slides(uow, actor.ws.id, actor.person.id, p, body.ids)
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.get("/presentations/{pid}/data")
async def data(pid: str, actor: CurrentActor, uow: Uow, fresh: bool = False) -> Response:
    p = await _get(actor, uow, pid)
    qc = deckdata.qctx(uow.session, actor.ws, actor.person, actor.member, actor.acl)
    return _json(await deckdata.deck_data(qc, actor.ws, p, viewer=actor.handle, fresh=fresh))


MEDIA = {
    "html": ("text/html; charset=utf-8", "html"), "pdf": ("application/pdf", "pdf"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
    "png": ("application/zip", "zip"), "csv": ("application/zip", "zip"),
}  # fmt: skip


MAX_BROWSER_RENDERS, RENDER_WAIT_S = 2, 5.0
_RENDER_SLOTS = asyncio.Semaphore(MAX_BROWSER_RENDERS)


@router.post("/presentations/{pid}/render")
async def render(pid: str, request: Request, actor: CurrentActor, uow: Uow, format: str = "html") -> Response:  # noqa: A002

    if format not in MEDIA:
        raise ApiError(400, "invalid_format", f"format must be one of {', '.join(MEDIA)}", pointer="/format")
    p = await _get(actor, uow, pid)
    qc = deckdata.qctx(uow.session, actor.ws, actor.person, actor.member, actor.acl)
    payload = await deckdata.deck_data(qc, actor.ws, p, viewer=actor.handle)
    if format in (
        "pdf",
        "png",
    ):  # headless Chromium: a few at a time, the rest are told to come back (review M7)
        try:
            await asyncio.wait_for(_RENDER_SLOTS.acquire(), timeout=RENDER_WAIT_S)
        except TimeoutError as exc:
            raise ApiError(
                503,
                "render_busy",
                "too many exports are running; try again in a moment",
                details={"retry_after": 10},
            ) from exc
        try:
            body = await exp.render(format, payload, BUNDLE, handle=actor.handle)
        finally:
            _RENDER_SLOTS.release()
    else:
        body = await exp.render(format, payload, BUNDLE, handle=actor.handle)
    _ev(uow, actor, p, "presentation.rendered", format=format)
    await uow.commit()
    media, ext = MEDIA[format]
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{_slug(p.title)}.{ext}"'},
    )


def _slug(t: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in t.lower()).strip("-") or "presentation"


class ShareIn(BaseModel):
    enabled: bool = True
    expires_in_days: int | None = Field(default=14, ge=1, le=365)


@router.post("/presentations/{pid}/share")
async def share(pid: str, body: ShareIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    check(actor, "view.share")
    out = svc.set_sharing(
        uow, actor.ws, actor.person.id, p, enabled=body.enabled, expires_in_days=body.expires_in_days
    )
    await uow.commit()
    return out


# ---------------------------------------------------------------- public (token) + deck shell
@public.get("/api/v1/public/p/{token}/data")
async def public_data(token: str, request: Request) -> dict[str, Any]:
    """Read-only external viewing: the deck is rendered with its *owner's* visibility, only while the token is live."""
    return await svc.public_data(request.app.state.session_factory, token)


def _shell(extra: str, *, framable: bool = False) -> HTMLResponse:
    """The deck page. `framable` lets the builder (same origin) preview it; share links are never framable."""
    if not BUNDLE.exists():
        raise ApiError(
            503,
            "bundle_missing",
            "the presentation bundle is not built (pnpm --filter @tt/presentation build)",
        )
    nonce = new_nonce()
    html = add_nonce(BUNDLE.read_text().replace("</head>", f"<script>{extra}</script></head>", 1), nonce)
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": html_csp(nonce, frame_ancestors="'self'" if framable else "'none'"),
        "X-Frame-Options": "SAMEORIGIN" if framable else "DENY",
    }
    return HTMLResponse(html, headers=headers)


_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _src(url: str, *parts: str, framable: bool = False) -> HTMLResponse:
    """Path segments are attacker-controlled (percent-decoded): accept only a safe alphabet, and JSON-encode with `<` escaped anyway."""
    if not all(_SAFE.match(p) for p in parts):
        raise ApiError(404, "not_found", "no such deck")
    literal = (
        json.dumps(url.format(*parts)).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    return _shell(f"window.__DECK_SRC__={literal};", framable=framable)


@shell.get("/p/t/{token}")
async def shell_token(token: str) -> HTMLResponse:
    return _src("/api/v1/public/p/{}/data", token)


@shell.get("/p/{ws}/{pid}")
async def shell_session(ws: str, pid: str) -> HTMLResponse:
    return _src("/api/v1/w/{}/presentations/{}/data", ws, pid, framable=True)
