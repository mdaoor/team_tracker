"""Applying a manager's choice on a review item: the per-kind write that resolving the item triggers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import (
    AssignmentRole,
    EventSource,
    ItemTypeKey,
    Priority,
    TargetType,
    ValueSource,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid, NotFound
from tt_server.domain.items import service as items
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import propagation, risks
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value
from tt_server.domain.plan import linkage
from tt_server.domain.projects.service import item_type


async def apply_choice(
    uow: UnitOfWork,
    ws: m.Workspace,
    row: m.ReviewItem,
    item: m.WorkItem,
    choice: dict[str, Any],
    actor_id: str,
) -> str:
    """Apply the manager's `choice` for a review row's kind to the item; returns the outcome text recorded on the row."""
    s = uow.session
    kind, sug = row.kind, row.suggestion or {}
    if kind == "unlinked_task":
        key = str(choice.get("deliverable") or "")
        if not key:
            raise Invalid(
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
            raise NotFound("not_found", f"unknown deliverable {key}", pointer="/choice/deliverable")
        await items.validate_nesting(s, ws, item.type_id, parent)
        await items.move_item(s, item, parent)

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
        row.suggestion = {
            **sug,
            "_undo": {"kind": "assignment", "assignment_id": asg.id, "assignee_id": item.assignee_id},
        }
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
            raise NotFound("not_found", f"unknown product {key}", pointer="/choice/product")
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
            raise NotFound("not_found", f"unknown risk {wanted}", pointer="/choice/risk")
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
                raise NotFound("not_found", "merge target not found", pointer="/choice/merge_into")
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
            raise NotFound("not_found", f"unknown {field} {value!r}")
        setattr(item, attr, row.id)
    else:
        return "acknowledged"
    return f"applied {field}"


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
        raise NotFound("not_found", "project not found", pointer="/choice")
    cfg = dict((project.settings or {}).get("jira") or {})
    fld = str(sug.get("field"))
    mp = dict(cfg.get(fld) or {})
    if sug.get("jira_status") is not None:  # inbound: Jira status -> a Team Tracker state
        state = str(choice.get("state") or "")
        if not state:
            raise Invalid(
                "invalid",
                "choose the Team Tracker state this Jira status means",
                pointer="/choice/state",
            )
        mp[state] = str(sug["jira_status"])
    else:
        target = str(choice.get("map_to") or "")
        if not target:
            raise Invalid("invalid", "choose the Jira name to map to", pointer="/choice/map_to")
        mp[str(sug["key"])] = target
    cfg[fld] = mp
    project.settings = {**(project.settings or {}), "jira": cfg}
    return f"mapped:{fld}"


async def _member(uow: UnitOfWork, ws: m.Workspace, handle: str) -> m.WorkspaceMember:
    row = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id,
            m.WorkspaceMember.handle == (handle if handle.startswith("@") else "@" + handle),
        )
    )
    if row is None:
        raise NotFound("not_found", f"unknown person {handle}", pointer="/choice/handle")
    return row


async def _project_by_key(uow: UnitOfWork, ws: m.Workspace, key: str) -> m.Project:
    row = await uow.session.scalar(
        select(m.Project).where(
            m.Project.workspace_id == ws.id, m.Project.key == key.upper(), m.Project.deleted_at.is_(None)
        )
    )
    if row is None:
        raise NotFound("not_found", f"unknown project {key}", pointer="/choice/project")
    return row


def func_lower(col: Any) -> Any:
    """SQL lower() of a column, for case-insensitive comparisons."""
    from sqlalchemy import func

    return func.lower(col)
