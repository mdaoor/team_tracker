"""Review queue (UX §3.2): suggestions the capture path could not apply, one owner per row (ADR-0006)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import utcnow
from tt_schema.enums import (
    EventSource,
    ProjectRole,
    ReportingKind,
    TargetType,
)

from tt_server.acl.context import item_ref
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, DomainError, Invalid, NotFound
from tt_server.domain.items import service as items
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay.defaults import suggest_default
from tt_server.domain.overlay.review_apply import apply_choice

KIND_ALIASES = {
    "deferred_stage": "stage_suggestion",
    "deferred_product": "product_suggestion",
    "deferred_risks": "risk_mention",
    "low_confidence": "low_confidence_match",
}


async def review_owner(session: AsyncSession, ws_id: str, item: m.WorkItem, project: m.Project) -> str | None:
    """The one manager who sees this item's rows (ADR-0006, amended by ADR-0042).

    The reporter's primary line manager first, so a line is never reviewed by the person who wrote it; then the project
    owner, then another project manager; for an Inbox with no line manager, the workspace's first member so work always
    reaches someone.
    """
    person = item.reporter_id or item.assignee_id
    line = await session.scalar(
        select(m.ReportingLine.manager_id).where(
            m.ReportingLine.workspace_id == ws_id,
            m.ReportingLine.person_id == person,
            m.ReportingLine.kind == ReportingKind.LINE,
            m.ReportingLine.is_primary.is_(True),
            m.ReportingLine.deleted_at.is_(None),
        )
    )
    if line and line != person:
        return line
    if project.kind.value != "inbox":
        if project.owner_id != person:
            return project.owner_id
        others = (
            await session.scalars(
                select(m.ProjectMember.person_id)
                .where(
                    m.ProjectMember.project_id == project.id,
                    m.ProjectMember.role.in_([ProjectRole.OWNER, ProjectRole.MANAGER]),
                    m.ProjectMember.person_id != person,
                )
                .order_by(m.ProjectMember.created_at)
            )
        ).first()
        if others:
            return others
    if project.kind.value != "inbox":
        return project.owner_id  # the reporter is the only manager there is: nobody else could review it
    first = await session.scalar(
        select(m.WorkspaceMember.person_id)
        .where(m.WorkspaceMember.workspace_id == ws_id)
        .order_by(m.WorkspaceMember.created_at, m.WorkspaceMember.id)
        .limit(1)
    )
    return first


async def _owned_rows(session: AsyncSession, ws_id: str, actor_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reviews = (
        await session.scalars(
            select(m.ReviewItem)
            .where(
                m.ReviewItem.workspace_id == ws_id,
                m.ReviewItem.status == "open",
                m.ReviewItem.deleted_at.is_(None),
            )
            .order_by(m.ReviewItem.created_at)
        )
    ).all()
    classifications = (
        await session.scalars(
            select(m.Classification)
            .where(
                m.Classification.workspace_id == ws_id,
                m.Classification.is_confirmed.is_(False),
                m.Classification.target_type == TargetType.WORK_ITEM,
                m.Classification.deleted_at.is_(None),
            )
            .order_by(m.Classification.created_at)
        )
    ).all()
    cache: dict[str, tuple[m.WorkItem, m.Project, str | None]] = {}
    handles = dict(
        (
            await session.execute(
                select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == ws_id
                )
            )
        ).all()
    )

    async def ctx(item_id: str) -> tuple[m.WorkItem, m.Project, str | None] | None:
        if item_id not in cache:
            item = await session.get(m.WorkItem, item_id)
            if item is None:
                return None
            project = await session.get(m.Project, item.project_id)
            assert project is not None  # noqa: S101
            cache[item_id] = (item, project, await review_owner(session, ws_id, item, project))
        return cache[item_id]

    for r in reviews:
        if r.target_type != TargetType.WORK_ITEM.value:
            continue
        c = await ctx(r.target_id)
        if c and c[2] == actor_id:
            kind = KIND_ALIASES.get(r.kind, r.kind)
            rows.append(
                {
                    "ref": f"review_item:{r.id}",
                    "kind": kind,
                    "raw_kind": r.kind,
                    "item_key": c[0].key,
                    "item_title": c[0].title,
                    "project_key": c[1].key,
                    "reporter": handles.get(c[0].reporter_id),
                    "suggestion": r.suggestion,
                    "suggested": await suggest_default(session, ws_id, kind, c[0], r.suggestion or {}),
                    "confidence": float(r.confidence) if r.confidence is not None else None,
                    "created_at": r.created_at.isoformat(),
                }
            )
    for cl in classifications:
        c = await ctx(cl.target_id)
        if c and c[2] == actor_id:
            dim, val = (
                await session.get(m.Dimension, cl.dimension_id),
                await session.get(m.DimensionValue, cl.value_id),
            )
            rows.append(
                {
                    "ref": f"classification:{cl.id}",
                    "kind": "unconfirmed_classification",
                    "raw_kind": "classification",
                    "item_key": c[0].key,
                    "item_title": c[0].title,
                    "project_key": c[1].key,
                    "reporter": handles.get(c[0].reporter_id),
                    "suggested": {
                        "choice": {"action": "confirm"},
                        "reason": "the suggested value",
                        "confidence": float(cl.confidence or 0.5),
                    },
                    "suggestion": {"dimension": dim.key if dim else None, "value": val.key if val else None},
                    "confidence": float(cl.confidence) if cl.confidence is not None else None,
                    "created_at": cl.created_at.isoformat(),
                }
            )
    return rows


async def list_queue(
    session: AsyncSession, ws_id: str, actor_id: str, kind: str | None = None
) -> list[dict[str, Any]]:
    """The review queue for a person: open suggestions and anomalies, optionally of one kind."""
    rows = await _owned_rows(session, ws_id, actor_id)
    return [r for r in rows if kind is None or r["kind"] == kind]


async def _load(uow: UnitOfWork, ws: m.Workspace, ref: str) -> tuple[str, Any]:
    kind, _, rid = ref.partition(":")
    s = uow.session
    if kind == "review_item":
        row = await s.scalar(
            select(m.ReviewItem).where(m.ReviewItem.id == rid, m.ReviewItem.workspace_id == ws.id)
        )
    elif kind == "classification":
        row = await s.scalar(
            select(m.Classification).where(m.Classification.id == rid, m.Classification.workspace_id == ws.id)
        )
    else:
        raise Invalid("invalid_ref", "ref must be review_item:<id> or classification:<id>")
    if row is None:
        raise NotFound("not_found", "review row not found")
    return kind, row


async def authorise(uow: UnitOfWork, kind: str, row: Any, actor_id: str) -> None:
    """The service checks itself (it is reached from resolve, bulk and confirm-all): the actor must be the row's review owner, or hold
    `review_queue.resolve` on the item. Anything else is a 404, as for an invisible object (ADR-0008)."""
    s = uow.session
    target_type = TargetType.WORK_ITEM.value if kind == "classification" else row.target_type
    item = await s.get(m.WorkItem, row.target_id) if target_type == TargetType.WORK_ITEM.value else None
    if item is None:
        acl = uow.context.get("acl")
        if acl is not None and acl.can("review_queue.resolve"):
            return  # workspace-level rows with no item (e.g. a person target)
        raise NotFound("not_found", "review row not found")
    project = await s.get(m.Project, item.project_id)
    assert project is not None  # noqa: S101
    if await review_owner(s, project.workspace_id, item, project) == actor_id:
        return
    acl = uow.context.get("acl")
    if acl is not None and acl.can("review_queue.resolve", await item_ref(s, item)):
        return
    raise NotFound("not_found", "review row not found")


async def resolve(
    uow: UnitOfWork, ws: m.Workspace, ref: str, choice: dict[str, Any], *, actor_id: str
) -> dict[str, Any]:
    """Perform the write behind a review row, learn glossary phrases, and close the row."""
    s = uow.session
    kind, row = await _load(uow, ws, ref)
    uow.context["actor_id"] = actor_id
    await authorise(uow, kind, row, actor_id)
    if kind == "classification":
        action = choice.get("action", "confirm")
        if action == "reject":
            await classify_svc.unclassify(uow, ws, row, by=actor_id)
        else:
            await classify_svc.confirm(uow, ws, row, by=actor_id)
        return {"ref": ref, "resolved": action}
    if row.status != "open":
        raise Conflict("already_resolved", "review row already resolved")
    item = await s.get(m.WorkItem, row.target_id)
    outcome = "ignored"
    if item is not None and not choice.get("ignore"):
        outcome = await apply_choice(uow, ws, row, item, choice, actor_id)
    else:
        row.suggestion = {**(row.suggestion or {}), "_undo": {"kind": "reopen"}}
    row.status = "resolved"
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=row.target_id,
        event_type="review.resolved",
        payload={"after": {"review_item_id": row.id, "kind": row.kind, "outcome": outcome}},
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    return {
        "ref": ref,
        "resolved": outcome,
        "undoable": "_undo" in (row.suggestion or {}),
        "learned": bool(((row.suggestion or {}).get("_undo") or {}).get("glossary_id")),
    }


async def undo(uow: UnitOfWork, ws: m.Workspace, ref: str, *, actor_id: str) -> dict[str, Any]:
    """Reverse a resolve while the row's write is still the latest one (UX 9). Not every outcome can be reversed."""
    s = uow.session
    kind, row = await _load(uow, ws, ref)
    uow.context["actor_id"] = actor_id
    await authorise(uow, kind, row, actor_id)
    if kind == "classification":
        if not row.is_confirmed:
            raise Conflict("cannot_undo", "this row is not resolved")
        row.is_confirmed, row.confirmed_by = False, None
        emit(
            uow, workspace_id=ws.id, actor_id=actor_id, entity_type=row.target_type, entity_id=row.target_id,
            event_type="review.reopened", payload={"after": {"ref": ref}}, source=EventSource.OVERLAY,
        )  # fmt: skip
        return {"ref": ref, "reopened": True}
    undo_info = (row.suggestion or {}).get("_undo")
    if row.status != "resolved" or not undo_info:
        raise Conflict("cannot_undo", "this resolution cannot be undone; fix the item directly")
    item = await s.get(m.WorkItem, row.target_id)
    if undo_info["kind"] == "project" and item is not None and undo_info.get("project_id"):
        back = await s.get(m.Project, undo_info["project_id"])
        if back is None or back.workspace_id != ws.id:
            raise Conflict("cannot_undo", "the original project no longer exists")
        await items.change_project(uow, ws, item, back, actor_id=actor_id)
        if undo_info.get("glossary_id"):
            entry = await s.get(m.GlossaryEntry, undo_info["glossary_id"])
            if entry is not None:
                entry.deleted_at = utcnow()
    elif undo_info["kind"] == "assignment" and item is not None:
        asg = await s.get(m.Assignment, undo_info["assignment_id"])
        if asg is not None:
            await s.delete(asg)
        item.assignee_id = undo_info.get("assignee_id")
    row.status = "open"
    row.suggestion = {k: v for k, v in (row.suggestion or {}).items() if k != "_undo"}
    emit(
        uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=row.target_id,
        event_type="review.reopened", payload={"after": {"review_item_id": row.id}}, source=EventSource.OVERLAY,
    )  # fmt: skip
    return {"ref": ref, "reopened": True}


async def unlearn(uow: UnitOfWork, ws: m.Workspace, ref: str, *, actor_id: str) -> dict[str, Any]:
    """ "Don't learn": drop the glossary phrase this resolve created, keeping the item where it was put."""
    s = uow.session
    kind, row = await _load(uow, ws, ref)
    await authorise(uow, kind, row, actor_id)
    gid = (((row.suggestion or {}).get("_undo") or {}).get("glossary_id")) if kind == "review_item" else None
    entry = await s.get(m.GlossaryEntry, gid) if gid else None
    if entry is None or entry.deleted_at is not None:
        return {"ref": ref, "unlearned": False}
    entry.deleted_at = utcnow()
    emit(
        uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=row.target_id,
        event_type="glossary.unlearned", payload={"after": {"phrase": entry.phrase}}, source=EventSource.OVERLAY,
    )  # fmt: skip
    return {"ref": ref, "unlearned": True}


async def bulk(
    uow: UnitOfWork, ws: m.Workspace, refs: list[str], action: str, *, actor_id: str
) -> dict[str, Any]:
    """Resolve many review rows with one action (confirm or ignore), returning the count done and the failures."""
    done, failed = 0, []
    for ref in refs:
        try:
            choice = (
                {"action": "confirm"}
                if action == "confirm" and ref.startswith("classification:")
                else {"ignore": True}
            )
            await resolve(uow, ws, ref, choice, actor_id=actor_id)
            done += 1
        except DomainError as exc:
            failed.append({"ref": ref, "error": exc.code})
    return {"resolved": done, "failed": failed}


async def confirm_all_suggested(uow: UnitOfWork, ws: m.Workspace, actor_id: str) -> int:
    """Confirm every pending suggestion in the queue; returns how many were confirmed."""
    rows = [
        r for r in await list_queue(uow.session, ws.id, actor_id) if r["kind"] == "unconfirmed_classification"
    ]
    for r in rows:
        await resolve(uow, ws, r["ref"], {"action": "confirm"}, actor_id=actor_id)
    return len(rows)
