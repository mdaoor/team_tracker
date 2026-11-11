"""Ingestion overlay hints: the only place a capture message may write overlay columns (recorded as rule/review)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select
from tt_schema.base import new_id
from tt_schema.client import ClientTaskRef
from tt_schema.enums import (
    AssignmentRole,
    EventSource,
    ItemTypeKey,
    TargetType,
    ValueSource,
)

from tt_server.db import models as m
from tt_server.domain.capture.ingest_context import Ctx
from tt_server.domain.capture.ingest_resolve import add_review, find_item, member_by_handle
from tt_server.domain.items import service as items
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import risks as risks_svc
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value
from tt_server.domain.projects.service import item_type

# ---------------------------------------------------------------- overlay hints


async def _resolve_ref(ctx: Ctx, ref: ClientTaskRef, project_id: str) -> m.WorkItem | None:
    found = await find_item(ctx, ref)
    if found or not ref.title:
        return found
    return await ctx.session.scalar(
        select(m.WorkItem).where(
            m.WorkItem.project_id == project_id,
            func.lower(m.WorkItem.title) == ref.title.lower(),
            m.WorkItem.deleted_at.is_(None),
        )
    )


async def apply_hints(ctx: Ctx, item: m.WorkItem, project: m.Project) -> None:
    """Apply optional hints (estimate, due date, labels) to the item, queueing low-confidence ones for review."""
    msg, session = ctx.msg, ctx.session
    changed: list[str] = []

    async def low(name: str, value: Any) -> bool:
        if ctx.conf_ok(name):
            return False
        await add_review(
            ctx,
            "low_confidence",
            item.id,
            {"field": name, "value": value},
            (msg.llm_confidence or {}).get(name),
        )
        return True

    if msg.tags and not await low("tags", msg.tags):
        existing = {
            t
            for t in (
                await session.scalars(
                    select(m.TagLink.tag_id).where(
                        m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id == item.id
                    )
                )
            ).all()
        }
        for name in msg.tags:
            tag, new = await items.get_or_create_tag(
                ctx.uow, ctx.ws, name.strip().lstrip("#"), provisional=True
            )
            if new:
                await add_review(ctx, "provisional_tag", item.id, {"tag_id": tag.id, "name": tag.name})
                ctx.emit(
                    "tag.created",
                    TargetType.WORK_ITEM,
                    item.id,
                    {"after": {"tag_id": tag.id, "name": tag.name}},
                )
            if tag.id not in existing:
                session.add(
                    m.TagLink(id=new_id(), tag_id=tag.id, target_type=TargetType.WORK_ITEM, target_id=item.id)
                )
                ctx.emit("tag.linked", TargetType.WORK_ITEM, item.id, {"after": {"tag_id": tag.id}})
                existing.add(tag.id)
    if msg.priority and not await low("priority", msg.priority.value):
        if item.priority != msg.priority:
            item.priority, item.priority_source = msg.priority, ValueSource.RULE
            ctx.emit(
                "item.priority_changed",
                TargetType.WORK_ITEM,
                item.id,
                {"after": {"priority": msg.priority.value}},
            )
            changed.append("priority")
    if msg.item_type and not await low("item_type", msg.item_type.value):
        t = await item_type(session, ctx.ws.id, ItemTypeKey(msg.item_type), project.id)
        if t.id != item.type_id:
            item.type_id = t.id
            changed.append("type_id")
    if msg.parent and not await low("parent", msg.parent.model_dump()):
        parent = await _resolve_ref(ctx, msg.parent, project.id)
        if parent is None or parent.id == item.id:
            await add_review(
                ctx, "unresolved_parent", item.id, {"parent": msg.parent.model_dump(exclude_none=True)}
            )
        else:
            try:
                await items.validate_nesting(session, ctx.ws, item.type_id, parent)
                await items.move_item(session, item, parent)
                ctx.emit(
                    "item.parent_changed", TargetType.WORK_ITEM, item.id, {"after": {"parent_id": parent.id}}
                )
            except Exception:  # invalid nesting / cycle -> review, never block
                await add_review(ctx, "invalid_parent", item.id, {"parent": parent.key})
    for field_name, model, fk in (
        ("milestone", m.Milestone, "milestone_id"),
        ("iteration", m.Iteration, "iteration_id"),
    ):
        value = getattr(msg, field_name)
        if value and not await low(field_name, value):
            row = await session.scalar(
                select(model).where(
                    model.project_id == project.id,
                    func.lower(model.name) == value.lower(),
                    model.deleted_at.is_(None),
                )
            )
            if row is None:
                await add_review(ctx, f"unknown_{field_name}", item.id, {field_name: value})
            else:
                setattr(item, fk, row.id)
                changed.append(fk)
    if msg.assignees and not await low("assignees", msg.assignees):
        for entry in msg.assignees:
            handle = entry.get("person", "")
            member = await member_by_handle(session, ctx.ws.id, handle) if handle else None
            if member is None:
                await add_review(
                    ctx, "unknown_person", item.id, {"person": handle, "role": entry.get("role")}
                )
                continue
            try:
                role = AssignmentRole(entry.get("role", "contributor"))
            except ValueError:
                role = AssignmentRole.CONTRIBUTOR
            current = await session.scalar(
                select(m.Assignment).where(
                    m.Assignment.work_item_id == item.id,
                    m.Assignment.person_id == member.person_id,
                    m.Assignment.role == role,
                    m.Assignment.unassigned_at.is_(None),
                )
            )
            if current:
                continue
            if role == AssignmentRole.OWNER:
                for old in (
                    await session.scalars(
                        select(m.Assignment).where(
                            m.Assignment.work_item_id == item.id,
                            m.Assignment.role == AssignmentRole.OWNER,
                            m.Assignment.unassigned_at.is_(None),
                        )
                    )
                ).all():
                    old.unassigned_at = ctx.at
                item.assignee_id = member.person_id
                changed.append("assignee_id")
            session.add(
                m.Assignment(
                    id=new_id(),
                    work_item_id=item.id,
                    person_id=member.person_id,
                    role=role,
                    assigned_at=ctx.at,
                )
            )
    if msg.custom_fields and not await low("custom_fields", list(msg.custom_fields)):
        for key, value in msg.custom_fields.items():
            definition = await session.scalar(
                select(m.CustomFieldDefinition).where(
                    m.CustomFieldDefinition.workspace_id == ctx.ws.id,
                    m.CustomFieldDefinition.key == key,
                    or_(
                        m.CustomFieldDefinition.project_id.is_(None),
                        m.CustomFieldDefinition.project_id == project.id,
                    ),
                    m.CustomFieldDefinition.deleted_at.is_(None),
                )
            )
            if definition is None:
                await add_review(ctx, "unknown_custom_field", item.id, {"key": key, "value": value})
                continue
            row = m.CustomFieldValue(id=new_id(), work_item_id=item.id, field_definition_id=definition.id)
            ft = definition.field_type.value
            try:
                if ft == "number":
                    row.value_number = Decimal(str(value))
                elif ft == "date":
                    row.value_date = date.fromisoformat(str(value))
                elif ft in ("text", "url"):
                    row.value_text = str(value)
                else:
                    row.value_json = value
            except (InvalidOperation, ValueError):
                await add_review(ctx, "invalid_custom_field", item.id, {"key": key, "value": value})
                continue
            session.add(row)
    if msg.stage and not await low("stage", msg.stage):
        process = await proc.get_process(session, ctx.ws.id)
        stage = next(
            (
                st
                for st in await proc.stages_of(session, process.id)
                if st.key == msg.stage.lower() or st.name.lower() == msg.stage.lower()
            ),
            None,
        )
        if stage is None:
            await add_review(ctx, "deferred_stage", item.id, {"stage": msg.stage})
        else:  # applied, but unconfirmed: a manager confirms it in the review queue
            await proc.place(
                ctx.uow,
                ctx.ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item.id,
                process=process,
                stage=stage,
                moved_by=ctx.actor.person_id,
                reason="reporter hint",
                source=EventSource.CLIENT,
                strict=False,
                confirmed=False,
                correlation_id=ctx.correlation_id,
            )
    if msg.product and not await low("product", msg.product):
        product = await session.scalar(
            select(m.Product).where(
                m.Product.workspace_id == ctx.ws.id,
                m.Product.deleted_at.is_(None),
                or_(
                    func.lower(m.Product.key) == msg.product.lower(),
                    func.lower(m.Product.name) == msg.product.lower(),
                ),
            )
        )
        if product is None:
            await add_review(ctx, "deferred_product", item.id, {"product": msg.product})
        else:
            dims = await ensure_builtin_dimensions(session, ctx.ws.id)
            value = await ensure_value(
                session,
                dims["product"],
                backing_type="product",
                backing_id=product.id,
                key=product.key,
                name=product.name,
                parent_backing_id=product.parent_product_id,
            )
            await classify_svc.classify(
                ctx.uow,
                ctx.ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item.id,
                dimension=dims["product"],
                value=value,
                source=ValueSource.RULE,
                set_by=ctx.actor.person_id,
                is_confirmed=False,
                confidence=(msg.llm_confidence or {}).get("product"),
                emit_event=False,
            )
    if msg.risks and not await low("risks", msg.risks):
        unresolved = []
        for rk in msg.risks:
            risk = await session.scalar(
                select(m.Risk)
                .join(m.Project, m.Project.id == m.Risk.project_id)
                .where(
                    m.Project.workspace_id == ctx.ws.id,
                    func.lower(m.Risk.key) == rk.lower(),
                    m.Risk.deleted_at.is_(None),
                )
            )
            if risk is None:
                unresolved.append(rk)
            else:
                await risks_svc.link_item(ctx.uow, ctx.ws, risk, item, actor_id=ctx.actor.person_id)
        if unresolved:
            await add_review(ctx, "deferred_risks", item.id, {"risks": unresolved})
    if changed:
        ctx.emit("item.updated", TargetType.WORK_ITEM, item.id, {"fields_changed": sorted(set(changed))})
