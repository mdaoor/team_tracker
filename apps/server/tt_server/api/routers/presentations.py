"""Phase 7 API: snapshot, presentations (+ slides, templates, data, render, share), insights, daily-log summary."""

from __future__ import annotations

import json
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
from tt_server.api.authz import check, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import row_dict
from tt_server.db import models as m
from tt_server.domain import deckdata, insights
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
async def templates(actor: CurrentActor) -> dict[str, Any]:
    return {
        "data": [
            {"key": k, "title": t, "slides": [s.kind for s in sl]} for k, (t, sl) in pres.TEMPLATES.items()
        ],
        "next_cursor": None,
    }


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


@router.post("/presentations:from-template", status_code=201, dependencies=[needs("view.save")])
async def from_template(body: FromTemplate, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await _check_refs(uow, actor, body.scope, body.weight_scheme_id)
    title, slides = pres.template_slides(body.template)
    p = m.Presentation(
        id=new_id(), workspace_id=actor.ws.id, owner_id=actor.person.id, title=body.title or title, scope=body.scope,
        range_days=body.range_days, weight_scheme_id=body.weight_scheme_id, slides=slides,
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    _ev(uow, actor, p, "presentation.created", template=body.template, slides=len(slides))
    await uow.commit()
    return deckdata.presentation_dict(p)


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


@router.post("/presentations/{pid}/render")
async def render(pid: str, request: Request, actor: CurrentActor, uow: Uow, format: str = "html") -> Response:  # noqa: A002
    from tt_server.exports import presentation as exp

    if format not in MEDIA:
        raise ApiError(400, "invalid_format", f"format must be one of {', '.join(MEDIA)}", pointer="/format")
    p = await _get(actor, uow, pid)
    qc = deckdata.qctx(uow.session, actor.ws, actor.person, actor.member, actor.acl)
    payload = await deckdata.deck_data(qc, actor.ws, p, viewer=actor.handle)
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
        return out


def _shell(extra: str) -> HTMLResponse:
    if not BUNDLE.exists():
        raise ApiError(
            503,
            "bundle_missing",
            "the presentation bundle is not built (pnpm --filter @tt/presentation build)",
        )
    html = BUNDLE.read_text()
    return HTMLResponse(
        html.replace("</head>", f"<script>{extra}</script></head>", 1),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@shell.get("/p/t/{token}")
async def shell_token(token: str) -> HTMLResponse:
    return _shell(f'window.__DECK_SRC__="/api/v1/public/p/{token}/data";')


@shell.get("/p/{ws}/{pid}")
async def shell_session(ws: str, pid: str) -> HTMLResponse:
    return _shell(f'window.__DECK_SRC__="/api/v1/w/{ws}/presentations/{pid}/data";')


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
        stmt = stmt.where(m.Insight.status == InsightStatus(status))
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
