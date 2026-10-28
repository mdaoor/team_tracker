"""Presentation CRUD, slide editing, sharing and the public token view: everything a deck endpoint does besides render.

The router parses and authorises (permission checks live there); these functions validate, write and emit. They never
commit: the caller owns the unit of work (ADR-0047).
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id, utcnow
from tt_schema.enums import TargetType

from tt_server.acl.context import AclContext, load_context
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain import deckdata
from tt_server.domain import presentation_templates as ptpl
from tt_server.domain import presentations as pres
from tt_server.domain.common import emit_after
from tt_server.domain.deck_variables import template_variables
from tt_server.domain.errors import Conflict, Fault, Forbidden, Invalid, NotFound


def announce(
    uow: UnitOfWork, ws_id: str, actor_id: str, p: m.Presentation, event: str, **payload: Any
) -> None:
    """Emit a presentation event. A presentation is a saved, shareable bundle of views: its events ride on the
    `saved_view` entity type (ADR-0021)."""
    emit_after(
        uow, workspace_id=ws_id, actor_id=actor_id, entity_type=TargetType.SAVED_VIEW,
        entity_id=p.id, event_type=event, title=p.title, **payload,
    )  # fmt: skip


async def get_visible(
    uow: UnitOfWork, ws_id: str, person_id: str, is_admin: bool, pid: str, *, write: bool = False
) -> m.Presentation:
    """A live presentation the person may see (own, shared, template or admin); with `write`, only owner or admin."""
    p = await uow.session.scalar(
        select(m.Presentation).where(
            m.Presentation.id == pid,
            m.Presentation.workspace_id == ws_id,
            m.Presentation.deleted_at.is_(None),
        )
    )
    if p is None or not (p.owner_id == person_id or p.is_shared or p.is_template or is_admin):
        raise NotFound("not_found", "presentation not found")
    if write and p.owner_id != person_id and not is_admin:
        raise Forbidden("forbidden", "only the owner may change a presentation")
    return p


async def check_refs(uow: UnitOfWork, ws_id: str, scope: str, scheme_id: str | None) -> None:
    """Reject a malformed scope reference or an unknown weight scheme."""
    pres.parse_scope_ref(scope)
    if scheme_id:
        await deckdata.scheme_by_ref(uow.session, ws_id, scheme_id)


async def template_gallery(
    uow: UnitOfWork, ws_id: str, person_id: str, is_admin: bool
) -> list[dict[str, Any]]:
    """The builder's "Start from template" list: the newest version of each template the person may use."""
    rows = (
        await uow.session.scalars(
            select(m.PresentationTemplate)
            .where(
                (m.PresentationTemplate.workspace_id == ws_id)
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
        if r.key in seen or not (r.is_builtin or r.is_shared or r.owner_id == person_id or is_admin):
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
    return data


async def create(
    uow: UnitOfWork,
    ws_id: str,
    person_id: str,
    *,
    title: str,
    scope: str,
    range_days: int,
    weight_scheme_id: str | None,
    theme: str,
    slides: list[pres.SlideIn],
    is_shared: bool,
) -> m.Presentation:
    """Create a presentation owned by the person."""
    await check_refs(uow, ws_id, scope, weight_scheme_id)
    p = m.Presentation(
        id=new_id(), workspace_id=ws_id, owner_id=person_id, title=title, scope=scope,
        range_days=range_days, weight_scheme_id=weight_scheme_id, theme=theme,
        slides=pres.normalise_slides(slides), is_shared=is_shared,
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    announce(uow, ws_id, person_id, p, "presentation.created", slides=len(p.slides))
    return p


async def create_from_template(
    uow: UnitOfWork,
    ws: m.Workspace,
    person: m.Person,
    member: m.WorkspaceMember,
    acl: AclContext,
    *,
    template: str,
    title: str | None,
    scope: str,
    range_days: int,
    weight_scheme_id: str | None,
    params: dict[str, Any],
) -> m.Presentation:
    """Copy a template into a new deck. The deck keeps `template_id`/`template_version` as provenance only (copy semantics)."""
    await check_refs(uow, ws.id, scope, weight_scheme_id)
    row = await ptpl.find(uow.session, ws.id, template, viewer_id=person.id)
    parsed, errors = ptpl.validate_body(row.body)
    if parsed is None:
        raise Fault("invalid_template", errors[0]["message"])
    variables, stats = await template_variables(uow, ws, person, member, acl, scope, range_days)
    new_title, slides = ptpl.instantiate(parsed, params, variables, stats)
    p = m.Presentation(
        id=new_id(), workspace_id=ws.id, owner_id=person.id, title=title or new_title, scope=scope,
        range_days=range_days, weight_scheme_id=weight_scheme_id, slides=pres.normalise_slides(slides),
        template_id=row.id, template_version=row.version,
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    announce(
        uow, ws.id, person.id, p, "presentation.created",
        template=row.key, template_version=row.version, slides=len(p.slides),
    )  # fmt: skip
    return p


async def list_visible(uow: UnitOfWork, ws_id: str, person_id: str, is_admin: bool) -> list[dict[str, Any]]:
    """Decks the person owns or may see (shared, or all for an admin), newest first, flagged with `mine`."""
    rows = (
        await uow.session.scalars(
            select(m.Presentation)
            .where(
                m.Presentation.workspace_id == ws_id,
                m.Presentation.deleted_at.is_(None),
                m.Presentation.is_template.is_(False),
            )
            .order_by(m.Presentation.updated_at.desc())
        )
    ).all()
    mine = [r for r in rows if r.owner_id == person_id or r.is_shared or is_admin]
    return [{**deckdata.presentation_dict(p), "mine": p.owner_id == person_id} for p in mine]


async def update(
    uow: UnitOfWork,
    ws_id: str,
    person_id: str,
    p: m.Presentation,
    data: dict[str, Any],
    slides: list[pres.SlideIn] | None,
) -> None:
    """Apply a partial update (`data` is the set fields; `slides` replaces the slide list when given)."""
    await check_refs(uow, ws_id, data.get("scope", p.scope), data.get("weight_scheme_id"))
    if "slides" in data:
        p.slides = pres.normalise_slides(slides or [])
        data.pop("slides")
    for k, v in data.items():
        setattr(p, k, v)
    announce(
        uow, ws_id, person_id, p, "presentation.updated",
        fields=sorted([*data, *(["slides"] if slides is not None else [])]),
    )  # fmt: skip


def delete(uow: UnitOfWork, ws_id: str, person_id: str, p: m.Presentation) -> None:
    """Soft-delete a deck and revoke its share link."""
    p.deleted_at = utcnow()
    p.share_token = None
    announce(uow, ws_id, person_id, p, "presentation.deleted")


async def restore(uow: UnitOfWork, ws_id: str, person_id: str, is_admin: bool, pid: str) -> m.Presentation:
    """Undo a delete (UX 6). The share link stays revoked: re-share explicitly."""
    p = await uow.session.scalar(
        select(m.Presentation).where(
            m.Presentation.id == pid,
            m.Presentation.workspace_id == ws_id,
            m.Presentation.deleted_at.is_not(None),
        )
    )
    if p is None or not (p.owner_id == person_id or is_admin):
        raise NotFound("not_found", "presentation not found")
    p.deleted_at = None
    announce(uow, ws_id, person_id, p, "presentation.restored")
    return p


async def duplicate(uow: UnitOfWork, ws_id: str, person_id: str, src: m.Presentation) -> m.Presentation:
    """Copy a deck (fresh slide ids) into a new one owned by the person."""
    p = m.Presentation(
        id=new_id(), workspace_id=ws_id, owner_id=person_id, title=f"{src.title} (copy)", scope=src.scope,
        range_days=src.range_days, weight_scheme_id=src.weight_scheme_id, theme=src.theme,
        slides=[{**s, "id": new_id()} for s in src.slides],
    )  # fmt: skip
    uow.session.add(p)
    await uow.session.flush()
    announce(uow, ws_id, person_id, p, "presentation.created", duplicated_from=src.id)
    return p


def _slide_ins(slides: list[dict[str, Any]]) -> list[pres.SlideIn]:
    return [pres.SlideIn(**{k: v for k, v in s.items() if k != "position"}) for s in slides]


def add_slide(
    uow: UnitOfWork, ws_id: str, person_id: str, p: m.Presentation, slide: pres.SlideIn, position: int | None
) -> None:
    """Insert a slide at `position` (default: the end)."""
    cur = _slide_ins(p.slides)
    at = len(cur) if position is None else max(0, min(position, len(cur)))
    cur.insert(at, slide)
    p.slides = pres.normalise_slides(cur)
    announce(uow, ws_id, person_id, p, "presentation.slide_added", kind=slide.kind)


def patch_slide(
    uow: UnitOfWork, ws_id: str, person_id: str, p: m.Presentation, sid: str, changes: dict[str, Any]
) -> None:
    """Merge `changes` into one slide."""
    if sid not in {s["id"] for s in p.slides}:
        raise NotFound("not_found", "slide not found")
    cur = [
        pres.SlideIn(
            **{k: v for k, v in ({**s, **changes} if s["id"] == sid else s).items() if k != "position"}
        )
        for s in p.slides
    ]
    p.slides = pres.normalise_slides(cur)
    announce(uow, ws_id, person_id, p, "presentation.updated", slide=sid)


def delete_slide(uow: UnitOfWork, ws_id: str, person_id: str, p: m.Presentation, sid: str) -> None:
    """Remove one slide."""
    keep = [s for s in p.slides if s["id"] != sid]
    if len(keep) == len(p.slides):
        raise NotFound("not_found", "slide not found")
    p.slides = pres.normalise_slides(_slide_ins(keep))
    announce(uow, ws_id, person_id, p, "presentation.updated", removed=sid)


def reorder_slides(uow: UnitOfWork, ws_id: str, person_id: str, p: m.Presentation, ids: list[str]) -> None:
    """Reorder the slides; `ids` must list every slide exactly once."""
    by_id = {s["id"]: s for s in p.slides}
    if sorted(ids) != sorted(by_id):
        raise Invalid("invalid_order", "ids must list every slide exactly once", pointer="/ids")
    p.slides = pres.normalise_slides(_slide_ins([by_id[i] for i in ids]))
    announce(uow, ws_id, person_id, p, "presentation.updated", reordered=True)


def set_sharing(
    uow: UnitOfWork,
    ws: m.Workspace,
    person_id: str,
    p: m.Presentation,
    *,
    enabled: bool,
    expires_in_days: int | None,
) -> dict[str, Any]:
    """Enable (a fresh unguessable token, optional expiry) or revoke the external share link."""
    if enabled:
        if not (ws.settings or {}).get("external_share"):
            raise Conflict(
                "not_enabled", "external sharing is disabled for this workspace (settings.external_share)"
            )
        p.share_token = "pt_" + secrets.token_urlsafe(24)
        p.share_expires_at = utcnow() + timedelta(days=expires_in_days) if expires_in_days else None
    else:
        p.share_token, p.share_expires_at = None, None
    announce(uow, ws.id, person_id, p, "presentation.shared", enabled=enabled)
    return {
        "token": p.share_token,
        "url": f"/p/t/{p.share_token}" if p.share_token else None,
        "expires_at": p.share_expires_at,
    }


async def public_data(factory: async_sessionmaker[AsyncSession], token: str) -> dict[str, Any]:
    """Read-only external viewing: the deck is rendered with its *owner's* visibility, only while the token is live."""
    async with factory() as session:
        p = await session.scalar(
            select(m.Presentation).where(
                m.Presentation.share_token == token, m.Presentation.deleted_at.is_(None)
            )
        )
        if p is None or (p.share_expires_at and p.share_expires_at < utcnow()):
            raise NotFound("not_found", "this link is not valid")
        ws = await session.get(m.Workspace, p.workspace_id)
        member = await session.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == p.workspace_id, m.WorkspaceMember.person_id == p.owner_id
            )
        )
        person = await session.get(m.Person, p.owner_id)
        if ws is None or member is None or person is None or not (ws.settings or {}).get("external_share"):
            raise NotFound("not_found", "this link is not valid")
        acl = await load_context(session, ws.id, person.id)
        qc = deckdata.qctx(session, ws, person, member, acl)
        out = await deckdata.deck_data(qc, ws, p, viewer=f"shared by {member.handle}")
        out["presentation"].pop("owner_id", None)
        return deckdata.redact_for_share(
            out, str((ws.settings or {}).get("external_share_redaction", "standard"))
        )
