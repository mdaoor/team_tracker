"""Presentation templates as data (plan P7 §7.5): CRUD, validate, preview, export/import, duplicate, share, save-as-template."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from tt_schema.base import new_id
from tt_schema.enums import TargetType

from tt_server.api.authz import needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.clock import person_today
from tt_server.db import models as m
from tt_server.domain import deckdata
from tt_server.domain import presentation_templates as tpl
from tt_server.domain import presentations as pres
from tt_server.domain.common import emit_after
from tt_server.domain.snapshot import assembler
from tt_server.query import service

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["presentation-templates"])


def _ev(uow: Uow, actor: Actor, row: m.PresentationTemplate, event: str, **after: Any) -> None:
    emit_after(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.SAVED_VIEW, entity_id=row.id,
        event_type=event, key=row.key, version=row.version, **after,
    )  # fmt: skip


async def _prime(uow: Uow) -> None:
    """Built-ins are loaded at migrate/start (`bootstrap.seed_reference_data`); nothing to do per request."""


async def _get(uow: Uow, actor: Actor, ref: str, *, write: bool = False) -> m.PresentationTemplate:
    await _prime(uow)
    row = await tpl.find(uow.session, actor.ws.id, ref, viewer_id=actor.person.id)
    if write and (
        row.is_builtin
        or row.workspace_id != actor.ws.id
        or not (row.owner_id == actor.person.id or actor.acl.is_admin)
    ):
        raise ApiError(
            403, "forbidden", "built-in and other people's templates are read-only; duplicate it first"
        )
    return row


@router.get("/presentation/slide-kinds")
async def slide_kinds() -> dict[str, Any]:
    return {"data": tpl.slide_kinds(), "variables": list(tpl.VARIABLES), "next_cursor": None}


@router.get("/presentation-templates")
async def list_templates(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await _prime(uow)
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
    out = []
    for r in rows:  # latest version per key; others' private templates stay private
        if r.key in seen or not (
            r.is_builtin or r.is_shared or r.owner_id == actor.person.id or actor.acl.is_admin
        ):
            continue
        seen.add(r.key)
        out.append({**tpl.view(r), "mine": r.owner_id == actor.person.id})
    return {"data": out, "next_cursor": None}


class BodyIn(BaseModel):
    body: dict[str, Any]


@router.post("/presentation-templates:validate")
async def validate(body: BodyIn, actor: CurrentActor) -> dict[str, Any]:
    parsed, errors = tpl.validate_body(body.body)
    return {"valid": not errors, "errors": errors}


@router.post("/presentation-templates", status_code=201, dependencies=[needs("view.save")])
async def create_template(body: BodyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await _prime(uow)
    parsed, errors = tpl.validate_body(body.body)
    if parsed is None:
        raise ApiError(
            400,
            "invalid_template",
            errors[0]["message"],
            pointer=errors[0]["pointer"],
            details={"errors": errors},
        )
    if await uow.session.scalar(
        select(m.PresentationTemplate.id).where(
            m.PresentationTemplate.workspace_id == actor.ws.id,
            m.PresentationTemplate.key == parsed.key,
            m.PresentationTemplate.deleted_at.is_(None),
        )
    ):
        raise ApiError(409, "conflict", f"a template with key {parsed.key!r} exists", pointer="/body/key")
    row = m.PresentationTemplate(
        id=new_id(), workspace_id=actor.ws.id, key=parsed.key, version=1, is_builtin=False, is_shared=False, owner_id=actor.person.id,
        body=parsed.model_dump(mode="json"),
    )  # fmt: skip
    uow.session.add(row)
    await uow.session.flush()
    _ev(uow, actor, row, "presentation.template_created")
    await uow.commit()
    return tpl.view(row)


@router.get("/presentation-templates/{ref}:export")
async def export(ref: str, actor: CurrentActor, uow: Uow) -> Response:
    row = await _get(uow, actor, ref)
    doc = {**row.body, "version": row.version}
    return Response(
        json.dumps(doc, indent=2, ensure_ascii=False), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{row.key}.tt-template.json"'},
    )  # fmt: skip


@router.get("/presentation-templates/{ref}")
async def get_template(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return tpl.view(await _get(uow, actor, ref))


@router.put("/presentation-templates/{ref}", dependencies=[needs("view.save")])
async def replace_template(ref: str, body: BodyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await _get(uow, actor, ref, write=True)
    parsed, errors = tpl.validate_body({**body.body, "key": row.key})
    if parsed is None:
        raise ApiError(
            400,
            "invalid_template",
            errors[0]["message"],
            pointer=errors[0]["pointer"],
            details={"errors": errors},
        )
    row.body = parsed.model_dump(mode="json")
    row.version += 1  # decks made from earlier versions keep their own copies
    _ev(uow, actor, row, "presentation.template_updated")
    await uow.commit()
    return tpl.view(row)


@router.delete("/presentation-templates/{ref}", status_code=204, dependencies=[needs("view.save")])
async def delete_template(ref: str, actor: CurrentActor, uow: Uow) -> Response:
    from tt_schema.base import utcnow

    row = await _get(uow, actor, ref, write=True)
    row.deleted_at = utcnow()
    _ev(uow, actor, row, "presentation.template_deleted")
    await uow.commit()
    return Response(status_code=204)


@router.post("/presentation-templates/{ref}:restore", dependencies=[needs("view.save")])
async def restore_template(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Undo a template delete (UX 6)."""
    row = await uow.session.scalar(
        select(m.PresentationTemplate)
        .where(
            m.PresentationTemplate.workspace_id == actor.ws.id,
            (m.PresentationTemplate.key == ref) | (m.PresentationTemplate.id == ref),
            m.PresentationTemplate.deleted_at.is_not(None),
        )
        .order_by(m.PresentationTemplate.deleted_at.desc())
    )
    if row is None or not (row.owner_id == actor.person.id or actor.acl.is_admin):
        raise ApiError(404, "not_found", "template not found")
    row.deleted_at = None
    _ev(uow, actor, row, "presentation.template_restored")
    await uow.commit()
    return tpl.view(row)


@router.post("/presentation-templates/{ref}:duplicate", status_code=201, dependencies=[needs("view.save")])
async def duplicate(ref: str, actor: CurrentActor, uow: Uow, key: str | None = None) -> dict[str, Any]:
    src = await _get(uow, actor, ref)
    new_key = key or f"{src.key}_copy"
    while await uow.session.scalar(
        select(m.PresentationTemplate.id).where(
            m.PresentationTemplate.workspace_id == actor.ws.id,
            m.PresentationTemplate.key == new_key,
            m.PresentationTemplate.deleted_at.is_(None),
        )
    ):
        new_key += "_2"
    body = {**src.body, "key": new_key, "name": f"{src.body['name']} (copy)"}
    row = m.PresentationTemplate(
        id=new_id(),
        workspace_id=actor.ws.id,
        key=new_key,
        version=1,
        is_builtin=False,
        owner_id=actor.person.id,
        body=body,
    )
    uow.session.add(row)
    await uow.session.flush()
    _ev(uow, actor, row, "presentation.template_created", copied_from=src.key)
    await uow.commit()
    return tpl.view(row)


class ShareIn(BaseModel):
    shared: bool = True


@router.post("/presentation-templates/{ref}:share", dependencies=[needs("view.share")])
async def share(ref: str, actor: CurrentActor, uow: Uow, body: ShareIn | None = None) -> dict[str, Any]:
    row = await _get(uow, actor, ref, write=True)
    row.is_shared = (body or ShareIn()).shared
    _ev(uow, actor, row, "presentation.template_shared", shared=row.is_shared)
    await uow.commit()
    return tpl.view(row)


@router.post("/presentation-templates:import", status_code=201, dependencies=[needs("view.save")])
async def import_template(body: BodyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Unknown slide kinds are rejected; unknown parameters of a slide's placeholders are errors; missing param defaults are filled in."""
    raw = {k: v for k, v in body.body.items() if k != "version"}
    return await create_template(BodyIn(body=raw), actor, uow)


class StatsScope(BaseModel):
    scope: str = "workspace"


async def _variables(
    uow: Uow, actor: Actor, scope: str, range_days: int
) -> tuple[dict[str, Any], dict[str, int]]:
    kind, ref = assembler.parse_scope(scope)
    qc = deckdata.qctx(uow.session, actor.ws, actor.person, actor.member, actor.acl)
    flt, label = await assembler._scope_filter(qc, kind, ref)  # noqa: SLF001

    stmt = await service.filtered_items(
        qc, {"and": [flt, {"not": {"type": {"in": ["epic", "phase", "deliverable"]}}}]}, m.WorkItem.id
    )
    items = int(await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    plan_stmt = await service.filtered_items(
        qc, {"and": [flt, {"type": {"in": ["phase", "deliverable"]}}]}, m.WorkItem.id
    )
    plan_items = int(await uow.session.scalar(select(func.count()).select_from(plan_stmt.subquery())) or 0)
    projects = (
        (
            await uow.session.scalar(
                select(func.count())
                .select_from(m.Project)
                .where(m.Project.workspace_id == actor.ws.id, m.Project.deleted_at.is_(None))
            )
            or 0
        )
        if kind == "workspace"
        else 1
    )
    pname = ""
    if kind == "project":
        pname = (
            await uow.session.scalar(
                select(m.Project.name).where(
                    m.Project.workspace_id == actor.ws.id, m.Project.key == ref.upper()
                )
            )
            or ""
        )
    today = person_today(actor.person)
    variables = {
        "scope.label": label, "scope.kind": kind, "scope.ref": ref, "scope.items": items, "scope.projects": projects,
        "project.key": ref.upper() if kind == "project" else "", "project.name": pname, "range.days": range_days,
        "range.from": (today.fromordinal(today.toordinal() - range_days)).isoformat(), "range.to": today.isoformat(),
        "viewer.handle": actor.handle, "workspace.name": actor.ws.name, "today": today.isoformat(),
    }  # fmt: skip
    return variables, {"scope.items": items, "scope.projects": projects, "scope.plan_items": plan_items}


class InstantiateIn(BaseModel):
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    params: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | None = None  # preview an unsaved draft


@router.post("/presentation-templates/{ref}:preview")
async def preview(ref: str, body: InstantiateIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """What starting from this template would give for a scope: the concrete slides, without saving anything."""
    parsed = None
    if body.body is not None:
        parsed, errors = tpl.validate_body(body.body)
        if parsed is None:
            raise ApiError(
                400,
                "invalid_template",
                errors[0]["message"],
                pointer=errors[0]["pointer"],
                details={"errors": errors},
            )
    else:
        parsed, _ = tpl.validate_body((await _get(uow, actor, ref)).body)
    assert parsed is not None  # noqa: S101
    variables, stats = await _variables(uow, actor, body.scope, body.range_days)
    title, slides = tpl.instantiate(parsed, body.params, variables, stats)
    return {"title": title, "slides": pres.normalise_slides(slides), "stats": stats}


class SaveAs(BaseModel):
    name: str = Field(min_length=1)
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,62}$")
    params_to_extract: list[str] = Field(
        default_factory=list
    )  # config values (e.g. project keys) lifted into parameters


@router.post("/presentations/{pid}:save-as-template", status_code=201, dependencies=[needs("view.save")])
async def save_as_template(
    pid: str, body: SaveAs, request: Request, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    p = await uow.session.scalar(
        select(m.Presentation).where(
            m.Presentation.id == pid,
            m.Presentation.workspace_id == actor.ws.id,
            m.Presentation.deleted_at.is_(None),
        )
    )
    if p is None or not (p.owner_id == actor.person.id or p.is_shared or actor.acl.is_admin):
        raise ApiError(404, "not_found", "presentation not found")
    key = (
        body.key
        or "".join(c if c.isalnum() else "_" for c in body.name.lower()).strip("_")[:60]
        or "template"
    )
    if not key[0].isalpha():
        key = "t_" + key
    lifted = {v: f"p{i + 1}" for i, v in enumerate(body.params_to_extract)}

    def lift(x: Any) -> Any:
        if isinstance(x, str):
            return "${" + lifted[x] + "}" if x in lifted else x
        if isinstance(x, dict):
            return {k: lift(v) for k, v in x.items()}
        if isinstance(x, list):
            return [lift(v) for v in x]
        return x

    slides = [
        {
            "kind": s["kind"], "title_template": s.get("title"), "subtitle_template": s.get("subtitle"), "config": lift(s.get("config", {})),
            "requires": pres.KIND_META[s["kind"]]["requires"], "render_empty": pres.KIND_META[s["kind"]]["render_empty"],
            "notes_template": s.get("notes"), "layout": s.get("layout", "full"),
        }
        for s in p.slides
    ]  # fmt: skip
    raw = {
        "schema_version": 1, "key": key, "name": body.name, "description": f"From the presentation “{p.title}”.",
        "params": [{"name": n, "type": "string", "required": False, "default": v} for v, n in lifted.items()], "slides": slides,
    }  # fmt: skip
    created = await create_template(BodyIn(body=raw), actor, uow)
    _ = request
    return created
