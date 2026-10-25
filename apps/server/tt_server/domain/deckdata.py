"""Everything a deck needs for one viewer: the snapshot plus the payload of every query-driven slide (ACL applied per slide)."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tt_filter import FilterError
from tt_schema.base import new_id, utcnow
from tt_schema.enums import InsightStatus, ScopeType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain import snapshot as snap
from tt_server.domain.presentations import QUERY_KINDS
from tt_server.query import service
from tt_server.query.compile import QueryCtx

CACHE_TTL = timedelta(minutes=15)


def qctx(
    session: AsyncSession,
    ws: m.Workspace,
    person: m.Person,
    member: m.WorkspaceMember,
    acl: AclContext,
    scheme_id: str | None = None,
) -> QueryCtx:
    return QueryCtx(
        session=session, ws_id=ws.id, acl=acl, person_id=person.id, handle=member.handle,
        tz=person.time_zone or "UTC", scheme_id=scheme_id,
    )  # fmt: skip


async def scheme_by_ref(session: AsyncSession, ws_id: str, ref: str | None) -> m.WeightScheme | None:
    if not ref:
        return None
    row = await session.scalar(
        select(m.WeightScheme).where(
            m.WeightScheme.workspace_id == ws_id, (m.WeightScheme.id == ref) | (m.WeightScheme.name == ref)
        )
    )
    if row is None:
        raise ApiError(400, "unknown_scheme", f"unknown weight scheme {ref!r}", pointer="/weight_scheme")
    return row


async def get_snapshot(
    qc: QueryCtx,
    ws: m.Workspace,
    *,
    scope: str,
    days: int,
    scheme: m.WeightScheme | None,
    viewer: str,
    fresh: bool = False,
    cache: bool = True,
) -> dict[str, Any]:
    """Cached per (viewer, scope, range, scheme): a snapshot is ACL-filtered, so it is never shared between people."""
    key = hashlib.sha256(f"{qc.person_id}|{scope}|{days}|{scheme.id if scheme else ''}".encode()).hexdigest()
    s = qc.session
    row = await s.scalar(
        select(m.SnapshotCache).where(m.SnapshotCache.workspace_id == ws.id, m.SnapshotCache.cache_key == key)
    )
    if cache and row is not None and not fresh and utcnow() - row.as_of < CACHE_TTL:
        return dict(row.body)
    body = await snap.assemble(
        qc, ws, scope=scope, days=days, viewer=viewer, scheme_name=scheme.name if scheme else "default"
    )
    if body.get("processes") is None:
        body.pop("processes", None)
    if cache:
        # one statement, so two requests assembling the same snapshot at once (builder preview + export) cannot collide
        now = utcnow()
        values = dict(
            workspace_id=ws.id, cache_key=key, person_id=qc.person_id, scope=scope, days=days,
            scheme_id=scheme.id if scheme else None, as_of=now, body=body,
        )  # fmt: skip
        await s.execute(
            pg_insert(m.SnapshotCache)
            .values(id=new_id(), **values)
            .on_conflict_do_update(
                index_elements=["workspace_id", "cache_key"],
                set_={"body": body, "as_of": now, "deleted_at": None},
            )
        )
        await s.commit()
    return body


async def _restricted(qc: QueryCtx, kind: str) -> bool:
    """A slide is `restricted` when rows exist in the workspace but none is visible to this viewer."""
    model = m.Violation if kind == "violations" else m.Habit
    s = qc.session
    total = await s.scalar(
        select(func.count())
        .select_from(model)
        .where(model.workspace_id == qc.ws_id, model.deleted_at.is_(None))
    )
    if not total:
        return False
    seen = await s.scalar(
        select(func.count()).select_from(
            visible(
                select(model.id).where(model.workspace_id == qc.ws_id, model.deleted_at.is_(None)),
                qc.acl,
                model,
            ).subquery()
        )
    )
    return not seen


async def slide_payload(
    qc: QueryCtx, slide: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any] | None:
    kind, cfg = slide["kind"], slide["config"]
    s = qc.session
    try:
        if kind == "view":
            v = await s.scalar(
                visible(
                    select(m.SavedView).where(
                        m.SavedView.id == cfg["saved_view_id"],
                        m.SavedView.workspace_id == qc.ws_id,
                        m.SavedView.deleted_at.is_(None),
                    ),
                    qc.acl,
                    m.SavedView,
                )
            )
            if v is None:
                return {"restricted": True}
            qc.scheme_id = v.weight_scheme_id or qc.scheme_id
            res = await service.query_items(
                qc, filter=v.filter, group_by=list(v.group_by), page={"size": service.MAX_PAGE},
                sort=[{"field": x.get("field", "priority"), "dir": x.get("dir", "desc")} for x in v.sort_by] or None,
            )  # fmt: skip
            return {
                "view": {"id": v.id, "name": v.name},
                "layout": cfg.get("layout") or v.layout.value,
                **res,
            }
        if kind == "chart":
            return await service.aggregate(
                qc,
                filter=cfg.get("filter") or {},
                rows=cfg["rows"],
                cols=cfg.get("cols"),
                measure=cfg.get("measure", "count"),
            )
        if kind == "table":
            return await service.query_items(
                qc,
                filter=cfg.get("filter") or {},
                sort=cfg.get("sort") or None,
                page={"size": min(int(cfg.get("limit", 50)), service.MAX_PAGE)},
            )
    except FilterError as exc:
        raise ApiError(
            400, "invalid_filter", exc.message, pointer=f"/slides/{slide['position']}/config/filter"
        ) from exc
    if kind in {"violations", "habits"}:
        return {"restricted": await _restricted(qc, kind)}
    if kind in {"insight", "project_detail"}:
        keys = (
            [cfg.get("scope") or cfg.get("project_key")]
            if (cfg.get("scope") or cfg.get("project_key"))
            else [p["key"] for p in snapshot["projects"]]
        )
        pids = {
            p.key: p.id
            for p in (
                await s.scalars(
                    visible(
                        select(m.Project).where(
                            m.Project.workspace_id == qc.ws_id,
                            m.Project.key.in_(keys),
                            m.Project.deleted_at.is_(None),
                        ),
                        qc.acl,
                        m.Project,
                    )
                )
            ).all()
        }
        rows = (
            await s.scalars(
                select(m.Insight)
                .where(
                    m.Insight.scope_type == ScopeType.PROJECT,
                    m.Insight.scope_id.in_(list(pids.values())),
                    m.Insight.status != InsightStatus.DISMISSED,
                    m.Insight.deleted_at.is_(None),
                )
                .order_by(m.Insight.generated_at.desc())
            )
        ).all()
        by_id = {v: k for k, v in pids.items()}
        return {
            "insights": [
                {
                    "id": r.id,
                    "project": by_id[r.scope_id],
                    "body": r.body,
                    "supporting_ids": r.supporting_ids,
                    "status": r.status.value,
                    "generated_at": r.generated_at.isoformat(),
                }
                for r in rows
            ]
        }
    return None


async def deck_data(
    qc: QueryCtx, ws: m.Workspace, p: m.Presentation, *, viewer: str, fresh: bool = False
) -> dict[str, Any]:
    scheme = await qc.session.get(m.WeightScheme, p.weight_scheme_id) if p.weight_scheme_id else None
    qc.scheme_id = scheme.id if scheme else None
    snapshot = await get_snapshot(
        qc, ws, scope=p.scope, days=p.range_days, scheme=scheme, viewer=viewer, fresh=fresh
    )
    slide_data: dict[str, Any] = {}
    for sl in p.slides:
        if sl["kind"] in QUERY_KINDS | {"violations", "habits", "insight", "project_detail"}:
            payload = await slide_payload(qc, sl, snapshot)
            if payload is not None:
                slide_data[sl["id"]] = payload
    return {"presentation": presentation_dict(p), "snapshot": snapshot, "slide_data": slide_data}


def presentation_dict(p: m.Presentation) -> dict[str, Any]:
    return {
        "id": p.id, "workspace_id": p.workspace_id, "owner_id": p.owner_id, "title": p.title, "scope": p.scope,
        "range_days": p.range_days, "weight_scheme_id": p.weight_scheme_id, "theme": p.theme, "slides": p.slides,
        "is_shared": p.is_shared, "is_template": p.is_template, "created_at": p.created_at, "updated_at": p.updated_at,
        "shared_externally": bool(p.share_token and (p.share_expires_at is None or p.share_expires_at > utcnow())),
        "source": p.source, "template_id": p.template_id, "template_version": p.template_version,
    }  # fmt: skip


async def refresh_cache(
    factory: Any, ws_id: str, *, older_than: timedelta = timedelta(minutes=10), keep_days: int = 7
) -> int:
    """Job body (every 15 min): re-assemble cached snapshots that are still being used, and warm each saved presentation's owner view.

    A row nobody has touched for `keep_days` is deleted rather than refreshed forever.
    """
    from tt_server.acl.context import load_context

    n = 0
    async with factory() as s:
        ws = await s.get(m.Workspace, ws_id)
        if ws is None:
            return 0
        rows = (
            await s.scalars(
                select(m.SnapshotCache).where(
                    m.SnapshotCache.workspace_id == ws_id, m.SnapshotCache.deleted_at.is_(None)
                )
            )
        ).all()
        warm = {
            (p.owner_id, p.scope, p.range_days, p.weight_scheme_id)
            for p in (
                await s.scalars(
                    select(m.Presentation).where(
                        m.Presentation.workspace_id == ws_id,
                        m.Presentation.deleted_at.is_(None),
                        m.Presentation.is_template.is_(False),
                    )
                )
            ).all()
        }
        have = {(r.person_id, r.scope, r.days, r.scheme_id) for r in rows}
        todo = [
            (r.person_id, r.scope, r.days, r.scheme_id, r) for r in rows if utcnow() - r.as_of >= older_than
        ]
        todo += [(*w, None) for w in warm - have]
        for r in rows:
            if utcnow() - r.updated_at > timedelta(days=keep_days):
                await s.delete(r)
        await s.commit()
    for person_id, scope, days, scheme_id, _ in todo:
        async with factory() as s:
            ws = await s.get(m.Workspace, ws_id)
            person = await s.get(m.Person, person_id)
            member = await s.scalar(
                select(m.WorkspaceMember).where(
                    m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.person_id == person_id
                )
            )
            scheme = await s.get(m.WeightScheme, scheme_id) if scheme_id else None
            if ws is None or person is None or member is None:
                continue
            qc = qctx(s, ws, person, member, await load_context(s, ws_id, person_id), scheme_id)
            try:
                await get_snapshot(
                    qc, ws, scope=scope, days=days, scheme=scheme, viewer=member.handle, fresh=True
                )
                n += 1
            except ApiError:
                continue  # the scope vanished (project deleted): the row ages out
    return n


def redact_for_share(out: dict[str, Any], level: str = "standard") -> dict[str, Any]:
    """An anonymous link viewer is not the owner: no wellbeing data and no live sessions ever; `strict` also pseudonymises people.

    The deck was assembled with the owner's visibility, so everything not listed here stays (that is what the owner chose to share)."""
    snap = out.get("snapshot") or {}
    snap["habits"] = []  # habits include wellbeing-adjacent signals about named people
    snap["now"] = []  # live sessions: who is working on what, right now
    for v in snap.get("violations", []):
        if v.get("category") == "wellbeing":
            v["person"] = None
    snap["violations"] = [v for v in snap.get("violations", []) if v.get("category") != "wellbeing"]
    for sd in (out.get("slide_data") or {}).values():
        if isinstance(sd, dict) and "restricted" not in sd:
            sd.pop("habits", None)
    if level != "strict":
        return out
    handles = sorted({p["handle"] for p in snap.get("people", []) if p.get("handle")})
    alias = {h: f"Person {i + 1}" for i, h in enumerate(handles)}
    pattern = (
        re.compile("|".join(re.escape(h) + r"(?![\w-])" for h in sorted(handles, key=len, reverse=True)))
        if handles
        else None
    )

    def walk(x: Any) -> Any:
        if isinstance(x, str):
            return pattern.sub(lambda mt: alias[mt.group(0)], x) if pattern else x
        if isinstance(x, list):
            return [walk(i) for i in x]
        if isinstance(x, dict):
            return {
                k: (None if k in ("email", "display_name", "name") and "handle" in x else walk(v))
                for k, v in x.items()
            }
        return x

    red: dict[str, Any] = walk(out)
    return red
