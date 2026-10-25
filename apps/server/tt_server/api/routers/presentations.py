"""Phase 7 API: snapshot, presentations (+ slides, templates, data, render, share), insights, daily-log summary."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import DailyLogStatus, InsightStatus, ScopeType, TargetType

from tt_server.acl.context import load_context
from tt_server.api.authz import check, check_project, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.params import enum_param
from tt_server.api.routers.core import _project, row_dict
from tt_server.csp import add_nonce, html_csp, new_nonce
from tt_server.db import models as m
from tt_server.domain import deckdata, insights
from tt_server.domain import generation_pack as gen
from tt_server.domain import presentation_templates as ptpl
from tt_server.domain import presentations as pres
from tt_server.domain.capture import dailylog
from tt_server.domain.common import emit
from tt_server.domain.snapshot import json_schema, parse_scope

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["presentation"])


def _json(body: Any) -> Response:
    """Straight `json.dumps`: FastAPI's `jsonable_encoder` walk costs about as much as the whole assembly on a 50 k-item snapshot."""
    return Response(json.dumps(body, default=str, separators=(",", ":")), media_type="application/json")


public = APIRouter(tags=["presentation"])
shell = APIRouter(tags=["presentation"])

BUNDLE = Path(__file__).resolve().parents[4] / "presentation" / "dist" / "presentation.html"


def _ev(uow: Uow, actor: Actor, p: m.Presentation, event: str, **payload: Any) -> None:
    # a presentation is a saved, shareable bundle of views: its events ride on the `saved_view` entity type (ADR-0021)
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.SAVED_VIEW,
        entity_id=p.id, event_type=event, payload={"after": {"title": p.title, **payload}},
    )  # fmt: skip


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
    p = await uow.session.scalar(
        select(m.Presentation).where(
            m.Presentation.id == pid,
            m.Presentation.workspace_id == actor.ws.id,
            m.Presentation.deleted_at.is_(None),
        )
    )
    if p is None or not (p.owner_id == actor.person.id or p.is_shared or p.is_template or actor.acl.is_admin):
        raise ApiError(404, "not_found", "presentation not found")
    if write and p.owner_id != actor.person.id and not actor.acl.is_admin:
        raise ApiError(403, "forbidden", "only the owner may change a presentation")
    return p


def _check_shared(actor: Actor, shared: bool) -> None:
    if shared:
        check(actor, "view.share")


async def _check_refs(uow: Uow, actor: Actor, scope: str, scheme_id: str | None) -> None:
    pres.parse_scope_ref(scope)
    if scheme_id:
        await deckdata.scheme_by_ref(uow.session, actor.ws.id, scheme_id)


@router.get("/presentations/templates")
async def templates(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """The gallery as the builder's "Start from template" list (the full CRUD lives under /presentation-templates)."""
    await ptpl.ensure_builtins(uow.session)
    await uow.session.commit()
    rows = (
        await uow.session.scalars(
            select(m.PresentationTemplate)
            .where(
                (m.PresentationTemplate.workspace_id == actor.ws.id)
                | m.PresentationTemplate.workspace_id.is_(None),
                m.PresentationTemplate.deleted_at.is_(None),
            )
            .order_by(
                m.PresentationTemplate.is_builtin.desc(),
                m.PresentationTemplate.key,
                m.PresentationTemplate.version.desc(),
            )
        )
    ).all()
    seen: set[str] = set()
    data = []
    for r in rows:
        if r.key in seen or not (
            r.is_builtin or r.is_shared or r.owner_id == actor.person.id or actor.acl.is_admin
        ):
            continue
        seen.add(r.key)
        data.append(
            {
                "key": r.key,
                "title": r.body["name"],
                "slides": [s["kind"] for s in r.body["slides"]],
                "params": r.body.get("params", []),
                "builtin": r.is_builtin,
            }
        )
    return {"data": data, "next_cursor": None}


@router.post("/presentations", status_code=201, dependencies=[needs("view.save")])
async def create(body: PresentationIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _check_shared(actor, body.is_shared)
    await _check_refs(uow, actor, body.scope, body.weight_scheme_id)
    p = m.Presentation(
        id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, title=body.title, scope=body.scope,
        range_days=body.range_days, weight_scheme_id=body.weight_scheme_id, theme=body.theme,
        slides=pres.normalise_slides(body.slides), is_shared=body.is_shared,
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    _ev(uow, actor, p, "presentation.created", slides=len(p.slides))
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
    from tt_server.api.routers.presentation_templates import _variables

    await _check_refs(uow, actor, body.scope, body.weight_scheme_id)
    await ptpl.ensure_builtins(uow.session)
    row = await ptpl.find(uow.session, actor.ws.id, body.template, viewer_id=actor.person.id)
    parsed, errors = ptpl.validate_body(row.body)
    if parsed is None:
        raise ApiError(500, "invalid_template", errors[0]["message"])
    variables, stats = await _variables(uow, actor, body.scope, body.range_days)
    title, slides = ptpl.instantiate(parsed, body.params, variables, stats)
    p = m.Presentation(
        id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, title=body.title or title, scope=body.scope,
        range_days=body.range_days, weight_scheme_id=body.weight_scheme_id, slides=pres.normalise_slides(slides),
        template_id=row.id, template_version=row.version,
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    _ev(
        uow,
        actor,
        p,
        "presentation.created",
        template=row.key,
        template_version=row.version,
        slides=len(p.slides),
    )
    await uow.commit()
    return deckdata.presentation_dict(p)


# ---------------------------------------------------------------- generation pack / import / author (plan P7 §7.6)
class PackIn(BaseModel):
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    weight_scheme_id: str | None = None
    brief: str = Field(default="", max_length=4000)
    redaction: str | None = None
    format: str = Field(default="zip", pattern="^(zip|json)$")


@router.post("/presentations:generation-pack", dependencies=[needs("view.share")])
async def generation_pack(body: PackIn, actor: CurrentActor, uow: Uow) -> Response:
    """Everything an outside LLM needs to draft a deck, as a zip (or the same files as JSON). Off unless the workspace allows it."""
    settings = actor.ws.settings or {}
    if not settings.get("external_packs"):
        raise ApiError(
            403,
            "external_packs_disabled",
            "this workspace does not allow exporting data for external LLMs; an admin can enable it",
        )
    level = body.redaction or str(settings.get("external_pack_redaction", "standard"))
    if level not in gen.REDACTIONS:
        raise ApiError(
            400, "invalid_redaction", f"redaction must be one of {list(gen.REDACTIONS)}", pointer="/redaction"
        )
    await _check_refs(uow, actor, body.scope, body.weight_scheme_id)
    scheme = await deckdata.scheme_by_ref(uow.session, actor.ws.id, body.weight_scheme_id)
    qc = deckdata.qctx(
        uow.session, actor.ws, actor.person, actor.member, actor.acl, scheme.id if scheme else None
    )
    snap = await deckdata.get_snapshot(
        qc,
        actor.ws,
        scope=body.scope,
        days=body.range_days,
        scheme=scheme,
        viewer=actor.handle,
        fresh=True,
        cache=False,
    )
    redacted = deckdata.redact_for_share({"snapshot": json.loads(json.dumps(snap, default=str))}, level)
    await ptpl.ensure_builtins(uow.session)
    rows = await uow.session.scalars(
        select(m.PresentationTemplate).where(m.PresentationTemplate.workspace_id.is_(None))
    )
    examples = [r.body for r in rows.all()]
    pack_id = new_id()
    files = gen.build_pack(
        snapshot=redacted["snapshot"], workspace_name=actor.ws.name, viewer=actor.handle, scope=body.scope,
        range_days=body.range_days, redaction=level, brief=body.brief, examples=examples, pack_id=pack_id,
    )  # fmt: skip
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.SAVED_VIEW, entity_id=pack_id,
        event_type="presentation.pack_generated",
        payload={"after": {"scope": body.scope, "range_days": body.range_days, "redaction": level, "files": len(files)}},
    )  # fmt: skip
    await uow.commit()
    if body.format == "json":
        return _json({"pack_id": pack_id, "files": {k: v.decode() for k, v in files.items()}})
    return Response(
        gen.to_zip(files), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tt-generation-pack-{pack_id[-8:]}.zip"'},
    )  # fmt: skip


class ImportIn(BaseModel):
    deck: dict[str, Any]
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    weight_scheme_id: str | None = None
    lenient: bool = False
    dry_run: bool = False


async def _known_projects(uow: Uow, actor: Actor, deck: dict[str, Any]) -> set[str]:
    keys = {
        (s.get("config") or {}).get("project_key")
        for s in deck.get("slides", [])
        if isinstance(s, dict) and isinstance(s.get("config"), dict)
    }
    ok: set[str] = set()
    for k in keys:
        if not isinstance(k, str) or not k:
            continue
        try:
            p = await _project(uow, actor.ws.id, k)
            await check_project(actor, uow, "item.read", p)
            ok.add(k)
        except ApiError:
            pass  # unknown and forbidden look the same: the deck cannot name a project the importer cannot read
    return ok


@router.post("/presentations:import", status_code=201, dependencies=[needs("view.save")])
async def import_deck(body: ImportIn, actor: CurrentActor, uow: Uow) -> Response:
    """Turn a deck an external model drafted into a presentation. Strict by default: every problem comes back with a JSON pointer."""
    await _check_refs(uow, actor, body.scope, body.weight_scheme_id)
    deck, errors, warnings = gen.check_deck(
        body.deck, lenient=body.lenient, known_projects=await _known_projects(uow, actor, body.deck)
    )
    if deck is None:
        raise ApiError(
            422,
            "invalid_deck",
            f"{len(errors)} problem(s) in the imported deck",
            details={"errors": errors, "warnings": warnings},
        )
    if body.dry_run:
        return _json({"valid": True, "slides": len(deck.slides), "warnings": warnings})
    p = m.Presentation(
        id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, title=deck.title, scope=body.scope,
        range_days=body.range_days, weight_scheme_id=body.weight_scheme_id, slides=pres.normalise_slides(deck.slides),
        source={"kind": "external_llm", "pack_id": deck.pack_id, "imported_at": utcnow().isoformat(), "lenient": body.lenient},
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    _ev(
        uow,
        actor,
        p,
        "presentation.created",
        slides=len(p.slides),
        source="external_llm",
        pack_id=deck.pack_id,
    )
    await uow.commit()
    return Response(
        json.dumps({**deckdata.presentation_dict(p), "warnings": warnings}, default=str),
        status_code=201, media_type="application/json",
    )  # fmt: skip


class AuthorIn(BaseModel):
    brief: str = Field(min_length=3, max_length=4000)
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    weight_scheme_id: str | None = None


@router.post("/presentations:author", status_code=201, dependencies=[needs("view.save")])
async def author(body: AuthorIn, request: Request, actor: CurrentActor, uow: Uow) -> Response:
    """ "Generate with AI": the gateway drafts a deck from a brief and the viewer's own snapshot digest; the draft goes through the same
    validation as an import (lenient), then lands as an ordinary deck the user can edit."""
    from tt_server.integrations.llm import GatewayBudgetExceeded, GatewayUnavailable

    gateway = request.app.state.llm
    if gateway is None:
        raise ApiError(503, "gateway_unavailable", "no LLM gateway is configured")
    await _check_refs(uow, actor, body.scope, body.weight_scheme_id)
    scheme = await deckdata.scheme_by_ref(uow.session, actor.ws.id, body.weight_scheme_id)
    qc = deckdata.qctx(
        uow.session, actor.ws, actor.person, actor.member, actor.acl, scheme.id if scheme else None
    )
    snap = await deckdata.get_snapshot(
        qc, actor.ws, scope=body.scope, days=body.range_days, scheme=scheme, viewer=actor.handle
    )
    digest = {"projects": [{"key": p.get("key"), "name": p.get("name")} for p in snap.get("projects", [])]}
    try:
        ans = await gateway.presentation_author(actor.ws.slug, body.brief, ptpl.slide_kinds(), digest)
    except GatewayBudgetExceeded as exc:
        raise ApiError(
            429,
            "budget_exceeded",
            "the LLM budget is used up for now",
            details={"retry_after_s": exc.retry_after},
        ) from exc
    except GatewayUnavailable as exc:
        raise ApiError(503, "gateway_unavailable", str(exc)) from exc
    draft = ans.output if isinstance(ans.output, dict) else {}
    deck, errors, warnings = gen.check_deck(
        draft, lenient=True, known_projects=await _known_projects(uow, actor, draft)
    )
    if deck is None:
        raise ApiError(
            422,
            "invalid_deck",
            "the model's draft was not usable",
            details={"errors": errors, "warnings": ans.warnings + warnings},
        )
    p = m.Presentation(
        id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, title=deck.title, scope=body.scope,
        range_days=body.range_days, weight_scheme_id=body.weight_scheme_id, slides=pres.normalise_slides(deck.slides),
        source={"kind": "gateway_llm", "run_id": ans.run_id, "model": ans.model, "brief": body.brief[:500]},
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    _ev(uow, actor, p, "presentation.created", slides=len(p.slides), source="gateway_llm", run_id=ans.run_id)
    await uow.commit()
    return Response(
        json.dumps({**deckdata.presentation_dict(p), "warnings": ans.warnings + warnings}, default=str),
        status_code=201, media_type="application/json",
    )  # fmt: skip


@router.get("/presentations")
async def list_presentations(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.Presentation)
            .where(
                m.Presentation.workspace_id == actor.ws.id,
                m.Presentation.deleted_at.is_(None),
                m.Presentation.is_template.is_(False),
            )
            .order_by(m.Presentation.updated_at.desc())
        )
    ).all()
    mine = [r for r in rows if r.owner_id == actor.person.id or r.is_shared or actor.acl.is_admin]
    return {
        "data": [{**deckdata.presentation_dict(p), "mine": p.owner_id == actor.person.id} for p in mine],
        "next_cursor": None,
    }


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
    await _check_refs(uow, actor, data.get("scope", p.scope), data.get("weight_scheme_id"))
    if "slides" in data:
        p.slides = pres.normalise_slides(body.slides or [])
        data.pop("slides")
    for k, v in data.items():
        setattr(p, k, v)
    _ev(
        uow,
        actor,
        p,
        "presentation.updated",
        fields=sorted([*data, *(["slides"] if body.slides is not None else [])]),
    )
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.delete("/presentations/{pid}", status_code=204)
async def delete_presentation(pid: str, actor: CurrentActor, uow: Uow) -> Response:
    p = await _get(actor, uow, pid, write=True)
    p.deleted_at = utcnow()
    p.share_token = None
    _ev(uow, actor, p, "presentation.deleted")
    await uow.commit()
    return Response(status_code=204)


@router.post("/presentations/{pid}:restore")
async def restore_presentation(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Undo a delete (UX 6). The share link stays revoked: re-share explicitly."""
    p = await uow.session.scalar(
        select(m.Presentation).where(
            m.Presentation.id == pid,
            m.Presentation.workspace_id == actor.ws.id,
            m.Presentation.deleted_at.is_not(None),
        )
    )
    if p is None or not (p.owner_id == actor.person.id or actor.acl.is_admin):
        raise ApiError(404, "not_found", "presentation not found")
    p.deleted_at = None
    _ev(uow, actor, p, "presentation.restored")
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.post("/presentations/{pid}:duplicate", status_code=201, dependencies=[needs("view.save")])
async def duplicate(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    src = await _get(actor, uow, pid)
    p = m.Presentation(
        id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, title=f"{src.title} (copy)", scope=src.scope,
        range_days=src.range_days, weight_scheme_id=src.weight_scheme_id, theme=src.theme,
        slides=[{**s, "id": new_id()} for s in src.slides],
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    _ev(uow, actor, p, "presentation.created", duplicated_from=src.id)
    await uow.commit()
    return deckdata.presentation_dict(p)


class SlideAdd(BaseModel):
    slide: pres.SlideIn
    position: int | None = None


@router.post("/presentations/{pid}/slides", status_code=201)
async def add_slide(pid: str, body: SlideAdd, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    cur = [pres.SlideIn(**{k: v for k, v in s.items() if k != "position"}) for s in p.slides]
    at = len(cur) if body.position is None else max(0, min(body.position, len(cur)))
    cur.insert(at, body.slide)
    p.slides = pres.normalise_slides(cur)
    _ev(uow, actor, p, "presentation.slide_added", kind=body.slide.kind)
    await uow.commit()
    return deckdata.presentation_dict(p)


class SlidePatch(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    config: dict[str, Any] | None = None
    notes: str | None = None
    layout: str | None = None
    live: bool | None = None


@router.patch("/presentations/{pid}/slides/{sid}")
async def patch_slide(pid: str, sid: str, body: SlidePatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    if sid not in {s["id"] for s in p.slides}:
        raise ApiError(404, "not_found", "slide not found")
    upd = body.model_dump(exclude_unset=True)
    cur = [
        pres.SlideIn(**{k: v for k, v in ({**s, **upd} if s["id"] == sid else s).items() if k != "position"})
        for s in p.slides
    ]
    p.slides = pres.normalise_slides(cur)
    _ev(uow, actor, p, "presentation.updated", slide=sid)
    await uow.commit()
    return deckdata.presentation_dict(p)


@router.delete("/presentations/{pid}/slides/{sid}")
async def delete_slide(pid: str, sid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    keep = [s for s in p.slides if s["id"] != sid]
    if len(keep) == len(p.slides):
        raise ApiError(404, "not_found", "slide not found")
    p.slides = pres.normalise_slides(
        [pres.SlideIn(**{k: v for k, v in s.items() if k != "position"}) for s in keep]
    )
    _ev(uow, actor, p, "presentation.updated", removed=sid)
    await uow.commit()
    return deckdata.presentation_dict(p)


class Reorder(BaseModel):
    ids: list[str]


@router.put("/presentations/{pid}/slides:reorder")
async def reorder(pid: str, body: Reorder, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _get(actor, uow, pid, write=True)
    by_id = {s["id"]: s for s in p.slides}
    if sorted(body.ids) != sorted(by_id):
        raise ApiError(400, "invalid_order", "ids must list every slide exactly once", pointer="/ids")
    p.slides = pres.normalise_slides(
        [pres.SlideIn(**{k: v for k, v in by_id[i].items() if k != "position"}) for i in body.ids]
    )
    _ev(uow, actor, p, "presentation.updated", reordered=True)
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
    from tt_server.exports import presentation as exp

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
    if body.enabled:
        if not (actor.ws.settings or {}).get("external_share"):
            raise ApiError(
                409,
                "not_enabled",
                "external sharing is disabled for this workspace (settings.external_share)",
            )
        p.share_token = "pt_" + secrets.token_urlsafe(24)
        p.share_expires_at = utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    else:
        p.share_token, p.share_expires_at = None, None
    _ev(uow, actor, p, "presentation.shared", enabled=body.enabled)
    await uow.commit()
    return {
        "token": p.share_token,
        "url": f"/p/t/{p.share_token}" if p.share_token else None,
        "expires_at": p.share_expires_at,
    }


# ---------------------------------------------------------------- public (token) + deck shell
@public.get("/api/v1/public/p/{token}/data")
async def public_data(token: str, request: Request) -> dict[str, Any]:
    """Read-only external viewing: the deck is rendered with its *owner's* visibility, only while the token is live."""
    async with request.app.state.session_factory() as session:
        p = await session.scalar(
            select(m.Presentation).where(
                m.Presentation.share_token == token, m.Presentation.deleted_at.is_(None)
            )
        )
        if p is None or (p.share_expires_at and p.share_expires_at < utcnow()):
            raise ApiError(404, "not_found", "this link is not valid")
        ws = await session.get(m.Workspace, p.workspace_id)
        member = await session.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == p.workspace_id, m.WorkspaceMember.person_id == p.owner_id
            )
        )
        person = await session.get(m.Person, p.owner_id)
        if ws is None or member is None or person is None or not (ws.settings or {}).get("external_share"):
            raise ApiError(404, "not_found", "this link is not valid")
        acl = await load_context(session, ws.id, person.id)
        qc = deckdata.qctx(session, ws, person, member, acl)
        out = await deckdata.deck_data(qc, ws, p, viewer=f"shared by {member.handle}")
        out["presentation"].pop("owner_id", None)
        return deckdata.redact_for_share(
            out, str((ws.settings or {}).get("external_share_redaction", "standard"))
        )


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


# ---------------------------------------------------------------- insights
class InsightGen(BaseModel):
    scope: str = Field(description="project key, or `all` for every visible project")


@router.post("/insights:generate", status_code=201, dependencies=[needs("view.save")])
async def generate(body: InsightGen, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    keys = [body.scope]
    if body.scope == "all":
        keys = [
            p.key
            for p in (
                await uow.session.scalars(
                    select(m.Project).where(
                        m.Project.workspace_id == actor.ws.id, m.Project.deleted_at.is_(None)
                    )
                )
            ).all()
        ]
    out = []
    for k in keys:
        proj, facts = await insights.facts_for_project(
            deckdata.qctx(uow.session, actor.ws, actor.person, actor.member, actor.acl), actor.ws, k
        )
        row, source = await insights.generate_project_insight(
            uow.session, actor.ws, proj, facts, request.app.state.llm, period=utcnow().date().isoformat()
        )
        emit(
            uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PROJECT, entity_id=proj.id,
            event_type="insight.generated", payload={"after": {"insight_id": row.id, "source": source}},
        )  # fmt: skip
        out.append({**row_dict(row), "project": proj.key, "source": source})
    await uow.commit()
    return {"data": out, "next_cursor": None}


@router.get("/insights")
async def list_insights(
    actor: CurrentActor, uow: Uow, project: str | None = None, status: str | None = None
) -> dict[str, Any]:
    from tt_server.acl.visibility import visible

    projs = (
        await uow.session.scalars(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == actor.ws.id, m.Project.deleted_at.is_(None)
                ),
                actor.acl,
                m.Project,
            )
        )
    ).all()
    by_id = {p.id: p.key for p in projs if project is None or p.key == project.upper()}
    stmt = select(m.Insight).where(
        m.Insight.scope_type == ScopeType.PROJECT,
        m.Insight.scope_id.in_(list(by_id)),
        m.Insight.deleted_at.is_(None),
    )
    if status:
        stmt = stmt.where(m.Insight.status == enum_param(InsightStatus, status, "status"))
    rows = (await uow.session.scalars(stmt.order_by(m.Insight.generated_at.desc()))).all()
    return {"data": [{**row_dict(r), "project": by_id[r.scope_id]} for r in rows], "next_cursor": None}


class InsightPatch(BaseModel):
    status: InsightStatus


@router.patch("/insights/{iid}")
async def patch_insight(iid: str, body: InsightPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    from tt_server.acl.visibility import visible

    row = await uow.session.get(m.Insight, iid)
    proj = (
        await uow.session.scalar(
            visible(
                select(m.Project).where(m.Project.id == row.scope_id, m.Project.workspace_id == actor.ws.id),
                actor.acl,
                m.Project,
            )
        )
        if row is not None
        else None
    )
    if row is None or proj is None:
        raise ApiError(404, "not_found", "insight not found")
    before = row.status
    row.status = body.status
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PROJECT, entity_id=proj.id,
        event_type="insight.status_changed", payload={"before": {"status": before.value}, "after": {"status": body.status.value, "insight_id": row.id}},
    )  # fmt: skip
    await uow.commit()
    return row_dict(row)


# ---------------------------------------------------------------- daily-log LLM summary (deferred from Phase 1)
@router.post("/daily-logs/{log_id}/summarise")
async def summarise_daily_log(log_id: str, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await dailylog.get_for(uow, actor.ws.id, log_id)
    if row.person_id != actor.person.id:
        raise ApiError(404, "not_found", "daily log not found")
    if row.status != DailyLogStatus.DRAFT:
        raise ApiError(409, "not_draft", "only a draft daily log can be re-summarised")
    gw = request.app.state.llm
    if gw is None:
        raise ApiError(409, "not_configured", "the LLM gateway is not configured")
    items = (
        await uow.session.execute(
            select(m.WorkItem.key, m.WorkItem.title).where(m.WorkItem.id.in_(row.item_ids or []))
        )
    ).all()
    facts = {
        "date": row.date.isoformat(),
        "minutes_total": row.minutes_total,
        "blockers": row.blockers,
        "items": [{"key": k, "title": t} for k, t in items],
    }
    from tt_server.integrations.llm import GatewayBudgetExceeded, GatewayUnavailable

    try:
        ans = await gw.summarise_daily_log(actor.ws.slug, facts)
    except (GatewayUnavailable, GatewayBudgetExceeded) as exc:
        raise ApiError(503, "gateway_unavailable", str(exc)) from exc
    out = ans.output or {}
    allowed = {k for k, _ in items}
    if not out.get("body") or not set(out.get("supporting_ids", [])) <= allowed:
        return {
            **dailylog.to_dict(row),
            "source": "deterministic",
            "warnings": ["summary not grounded in the day's items"],
        }
    row.summary = str(out["body"]).strip()
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.DAILY_LOG, entity_id=row.id,
        event_type="dailylog.summarised", payload={"after": {"date": row.date.isoformat(), "run_id": ans.run_id}},
    )  # fmt: skip
    await uow.commit()
    return {**dailylog.to_dict(row), "source": "gateway", "warnings": ans.warnings}
