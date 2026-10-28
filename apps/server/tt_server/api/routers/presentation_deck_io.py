"""Deck generation pack, import and author endpoints (plan P7 §7.6)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import TargetType

from tt_server.api.authz import check_project, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _project
from tt_server.api.routers.presentations import _check_refs, _ev, _json
from tt_server.db import models as m
from tt_server.domain import deckdata
from tt_server.domain import generation_pack as gen
from tt_server.domain import presentation_templates as ptpl
from tt_server.domain import presentations as pres
from tt_server.domain.common import emit
from tt_server.domain.errors import DomainError
from tt_server.integrations.llm import GatewayBudgetExceeded, GatewayUnavailable

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["presentation"])


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
        except DomainError:
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
    __module__ = "tt_server.api.routers.presentations"  # pins the published OpenAPI schema name

    brief: str = Field(min_length=3, max_length=4000)
    scope: str = "workspace"
    range_days: int = Field(default=14, ge=1, le=366)
    weight_scheme_id: str | None = None


AuthorIn.model_rebuild(_types_namespace={"Field": Field})


@router.post("/presentations:author", status_code=201, dependencies=[needs("view.save")])
async def author(body: AuthorIn, request: Request, actor: CurrentActor, uow: Uow) -> Response:
    """ "Generate with AI": the gateway drafts a deck from a brief and the viewer's own snapshot digest; the draft goes through the same
    validation as an import (lenient), then lands as an ordinary deck the user can edit."""

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
