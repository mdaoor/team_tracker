"""Review queue (UX §3.2): suggestions the capture path could not apply, one owner per row (ADR-0006)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    AssignmentRole,
    EventSource,
    ItemTypeKey,
    Priority,
    ProjectRole,
    ReportingKind,
    TargetType,
    ValueSource,
)

from tt_server.acl.context import item_ref
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import propagation, risks
from tt_server.domain.overlay.defaults import suggest_default
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value
from tt_server.domain.projects.service import item_type

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
                    "suggested": {"choice": {"action": "confirm"}, "reason": "the suggested value", "confidence": float(cl.confidence or 0.5)},
                    "suggestion": {"dimension": dim.key if dim else None, "value": val.key if val else None},
                    "confidence": float(cl.confidence) if cl.confidence is not None else None,
                    "created_at": cl.created_at.isoformat(),
                }
            )
    return rows


async def list_queue(
    session: AsyncSession, ws_id: str, actor_id: str, kind: str | None = None
) -> list[dict[str, Any]]:
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
        raise ApiError(400, "invalid_ref", "ref must be review_item:<id> or classification:<id>")
    if row is None:
        raise ApiError(404, "not_found", "review row not found")
    return kind, row


async def _member(uow: UnitOfWork, ws: m.Workspace, handle: str) -> m.WorkspaceMember:
    row = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id,
            m.WorkspaceMember.handle == (handle if handle.startswith("@") else "@" + handle),
        )
    )
    if row is None:
        raise ApiError(404, "not_found", f"unknown person {handle}", pointer="/choice/handle")
    return row


async def _project_by_key(uow: UnitOfWork, ws: m.Workspace, key: str) -> m.Project:
    row = await uow.session.scalar(
        select(m.Project).where(
            m.Project.workspace_id == ws.id, m.Project.key == key.upper(), m.Project.deleted_at.is_(None)
        )
    )
    if row is None:
        raise ApiError(404, "not_found", f"unknown project {key}", pointer="/choice/project")
    return row


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
        raise ApiError(404, "not_found", "review row not found")
    project = await s.get(m.Project, item.project_id)
    assert project is not None  # noqa: S101
    if await review_owner(s, project.workspace_id, item, project) == actor_id:
        return
    acl = uow.context.get("acl")
    if acl is not None and acl.can("review_queue.resolve", await item_ref(s, item)):
        return
    raise ApiError(404, "not_found", "review row not found")


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
        raise ApiError(409, "already_resolved", "review row already resolved")
    item = await s.get(m.WorkItem, row.target_id)
    outcome = "ignored"
    if item is not None and not choice.get("ignore"):
        outcome = await _apply_choice(uow, ws, row, item, choice, actor_id)
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
            raise ApiError(409, "cannot_undo", "this row is not resolved")
        row.is_confirmed, row.confirmed_by = False, None
        emit(
            uow, workspace_id=ws.id, actor_id=actor_id, entity_type=row.target_type, entity_id=row.target_id,
            event_type="review.reopened", payload={"after": {"ref": ref}}, source=EventSource.OVERLAY,
        )  # fmt: skip
        return {"ref": ref, "reopened": True}
    undo_info = (row.suggestion or {}).get("_undo")
    if row.status != "resolved" or not undo_info:
        raise ApiError(409, "cannot_undo", "this resolution cannot be undone; fix the item directly")
    item = await s.get(m.WorkItem, row.target_id)
    if undo_info["kind"] == "project" and item is not None and undo_info.get("project_id"):
        back = await s.get(m.Project, undo_info["project_id"])
        if back is None or back.workspace_id != ws.id:
            raise ApiError(409, "cannot_undo", "the original project no longer exists")
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
    """"Don't learn": drop the glossary phrase this resolve created, keeping the item where it was put."""
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


async def _apply_choice(
    uow: UnitOfWork,
    ws: m.Workspace,
    row: m.ReviewItem,
    item: m.WorkItem,
    choice: dict[str, Any],
    actor_id: str,
) -> str:
    s = uow.session
    kind, sug = row.kind, row.suggestion or {}
    if kind == "unlinked_task":
        key = str(choice.get("deliverable") or "")
        if not key:
            raise ApiError(
                400,
                "invalid",
                "choose a deliverable, or keep the task unlinked",
                pointer="/choice/deliverable",
            )
        parent = await s.scalar(
            select(m.WorkItem).where(
                m.WorkItem.project_id == item.project_id, func_lower(m.WorkItem.key) == key.lower()
            )
        )
        if parent is None:
            raise ApiError(404, "not_found", f"unknown deliverable {key}", pointer="/choice/deliverable")
        await items.validate_nesting(s, ws, item.type_id, parent)
        await items.move_item(s, item, parent)
        from tt_server.domain.plan import linkage

        await linkage.refresh_ancestors(s, item)
        emit(
            uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=item.id, event_type="item.parent_changed",
            payload={"after": {"parent_id": parent.id, "deliverable": parent.key}, "cause": "review"}, source=EventSource.OVERLAY,
        )  # fmt: skip
        return f"linked:{parent.key}"
    if kind in ("plan_slip", "plan_conflict"):
        return "acknowledged"
    if kind == "jira_mapping_gap":
        return await _apply_jira_gap(uow, row, choice)
    if kind == "jira_sync_failed":
        return "acknowledged"
    if kind == "unclassified_project":
        project = await _project_by_key(uow, ws, str(choice.get("project", "")))
        old = await s.get(m.Project, item.project_id)
        undo: dict[str, Any] = {"kind": "project", "project_id": old.id if old else None, "glossary_id": None}
        await items.change_project(uow, ws, item, project, actor_id=actor_id)
        phrase = str(sug.get("phrase") or "").strip()
        if (
            phrase
            and choice.get("learn", True)
            and not await s.scalar(
                select(m.GlossaryEntry.id).where(
                    m.GlossaryEntry.workspace_id == ws.id,
                    m.GlossaryEntry.deleted_at.is_(None),
                    func_lower(m.GlossaryEntry.phrase) == phrase.lower(),
                )
            )
        ):
            entry = m.GlossaryEntry(
                id=new_id(),
                workspace_id=ws.id,
                project_id=project.id,
                phrase=phrase,
                resolves_to_type="project",
                resolves_to_id=project.id,
                source="learned",
                hits=1,
            )
            s.add(entry)
            undo["glossary_id"] = entry.id
            emit(
                uow,
                workspace_id=ws.id,
                actor_id=actor_id,
                entity_type=TargetType.WORK_ITEM,
                entity_id=item.id,
                event_type="glossary.learned",
                payload={"after": {"phrase": phrase, "project": project.key}},
                source=EventSource.OVERLAY,
            )
        row.suggestion = {**sug, "_undo": undo}
        return f"moved to {project.key}"
    if kind == "unknown_person":
        member = await _member(uow, ws, str(choice.get("handle", "")))
        role = AssignmentRole(sug.get("role") or choice.get("role") or "contributor")
        asg = m.Assignment(id=new_id(), work_item_id=item.id, person_id=member.person_id, role=role)
        s.add(asg)
        row.suggestion = {**sug, "_undo": {"kind": "assignment", "assignment_id": asg.id, "assignee_id": item.assignee_id}}
        if role == AssignmentRole.OWNER:
            item.assignee_id = member.person_id
        return f"assigned {member.handle}"
    if kind in ("deferred_stage", "stage_suggestion"):
        process = await proc.get_process(s, ws.id)
        stage = await proc.get_stage(s, process, str(choice.get("stage") or sug.get("stage") or ""))
        await proc.place(
            uow,
            ws,
            target_type=TargetType.WORK_ITEM,
            target_id=item.id,
            process=process,
            stage=stage,
            moved_by=actor_id,
            reason="review queue",
        )
        return f"placed on {stage.key}"
    if kind in ("deferred_product", "product_suggestion"):
        key = str(choice.get("product") or sug.get("product") or "").upper()
        product = await s.scalar(
            select(m.Product).where(
                m.Product.workspace_id == ws.id, m.Product.key == key, m.Product.deleted_at.is_(None)
            )
        )
        if product is None:
            raise ApiError(404, "not_found", f"unknown product {key}", pointer="/choice/product")
        dims = await ensure_builtin_dimensions(s, ws.id)
        value = await ensure_value(
            s,
            dims["product"],
            backing_type="product",
            backing_id=product.id,
            key=product.key,
            name=product.name,
            parent_backing_id=product.parent_product_id,
        )
        await classify_svc.classify(
            uow,
            ws,
            target_type=TargetType.WORK_ITEM,
            target_id=item.id,
            dimension=dims["product"],
            value=value,
            source=ValueSource.MANUAL,
            set_by=actor_id,
        )
        return f"product {product.key}"
    if kind in ("deferred_risks", "risk_mention"):
        wanted = choice.get("risk") or next(iter(sug.get("risks") or []), None)
        risk = await s.scalar(
            select(m.Risk)
            .join(m.Project, m.Project.id == m.Risk.project_id)
            .where(m.Project.workspace_id == ws.id, m.Risk.key == str(wanted))
        )
        if risk is None:
            raise ApiError(404, "not_found", f"unknown risk {wanted}", pointer="/choice/risk")
        await risks.link_item(uow, ws, risk, item, actor_id=actor_id)
        return f"linked {risk.key}"
    if kind == "low_confidence":
        return await _apply_low_confidence(uow, ws, item, sug, actor_id)
    if kind == "provisional_tag":
        tag = await s.get(m.Tag, str(sug.get("tag_id")))
        if tag is None:
            return "ignored"
        if choice.get("merge_into"):
            dst = await s.get(m.Tag, str(choice["merge_into"]))
            if dst is None or dst.workspace_id != ws.id:
                raise ApiError(404, "not_found", "merge target not found", pointer="/choice/merge_into")
            for link in (await s.scalars(select(m.TagLink).where(m.TagLink.tag_id == tag.id))).all():
                link.tag_id = dst.id
            tag.deleted_at = row.created_at
            return f"merged into {dst.name}"
        tag.is_provisional = False
        return f"tag {tag.name} confirmed"
    if kind == "missing_estimate" and choice.get("value") is not None:
        from decimal import Decimal

        from tt_schema.enums import EstimationUnit

        await items.record_estimate(
            uow,
            item,
            person_id=actor_id,
            value=Decimal(str(choice["value"])),
            unit=EstimationUnit(choice.get("unit", "hours")),
        )
        return "estimate set"
    return "acknowledged"


async def _apply_low_confidence(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, sug: dict[str, Any], actor_id: str
) -> str:
    field, value = sug.get("field"), sug.get("value")
    if field == "priority":
        await propagation.set_item_priority(uow, ws, item, Priority(value), actor=actor_id)
    elif field == "item_type":
        item.type_id = (await item_type(uow.session, ws.id, ItemTypeKey(value), item.project_id)).id
    elif field in ("milestone", "iteration"):
        model, attr = (m.Milestone, "milestone_id") if field == "milestone" else (m.Iteration, "iteration_id")
        row = await uow.session.scalar(
            select(model).where(model.project_id == item.project_id, model.name == value)
        )
        if row is None:
            raise ApiError(404, "not_found", f"unknown {field} {value!r}")
        setattr(item, attr, row.id)
    else:
        return "acknowledged"
    return f"applied {field}"


def func_lower(col: Any) -> Any:
    from sqlalchemy import func

    return func.lower(col)


async def bulk(
    uow: UnitOfWork, ws: m.Workspace, refs: list[str], action: str, *, actor_id: str
) -> dict[str, Any]:
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
        except ApiError as exc:
            failed.append({"ref": ref, "error": exc.code})
    return {"resolved": done, "failed": failed}


async def confirm_all_suggested(uow: UnitOfWork, ws: m.Workspace, actor_id: str) -> int:
    rows = [
        r for r in await list_queue(uow.session, ws.id, actor_id) if r["kind"] == "unconfirmed_classification"
    ]
    for r in rows:
        await resolve(uow, ws, r["ref"], {"action": "confirm"}, actor_id=actor_id)
    return len(rows)


async def _apply_jira_gap(uow: UnitOfWork, row: m.ReviewItem, choice: dict[str, Any]) -> str:
    """One-click mapping fix: `{"map_to": "<Jira name>"}` (or `{"state": "<state key>"}` for an unmapped Jira status) edits the project's mapping."""
    s = uow.session
    sug = row.suggestion or {}
    project = await s.scalar(
        select(m.Project).where(
            m.Project.key == sug.get("project"), m.Project.workspace_id == row.workspace_id
        )
    )
    if project is None:
        raise ApiError(404, "not_found", "project not found", pointer="/choice")
    cfg = dict((project.settings or {}).get("jira") or {})
    fld = str(sug.get("field"))
    mp = dict(cfg.get(fld) or {})
    if sug.get("jira_status") is not None:  # inbound: Jira status -> a Team Tracker state
        state = str(choice.get("state") or "")
        if not state:
            raise ApiError(
                400,
                "invalid",
                "choose the Team Tracker state this Jira status means",
                pointer="/choice/state",
            )
        mp[state] = str(sug["jira_status"])
    else:
        target = str(choice.get("map_to") or "")
        if not target:
            raise ApiError(400, "invalid", "choose the Jira name to map to", pointer="/choice/map_to")
        mp[str(sug["key"])] = target
    cfg[fld] = mp
    project.settings = {**(project.settings or {}), "jira": cfg}
    return f"mapped:{fld}"
