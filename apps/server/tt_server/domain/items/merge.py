"""Merge work items (Phase 14, ADR-0050).

Sources are folded into a target. Capture rows (activities, time, evidence, comments, attachments) are *re-pointed by
reference*: their content and identity are untouched, so total minutes and evidence are preserved; only the item they hang
off changes. Tags, links, assignments, risk links and classifications are merged without duplicates (a manual
classification beats an inherited one). Sources are soft-deleted with `external_refs.merged_into`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select, update
from tt_schema.base import utcnow
from tt_schema.enums import EventSource, TargetType, ValueSource

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, rowcount
from tt_server.domain.errors import Invalid, NotFound


@dataclass
class MergePlan:
    """The computed outcome of merging work items: sources, target (or a new item) and affected row counts."""

    sources: list[m.WorkItem]
    target: m.WorkItem | None  # None = a new item will be created
    new_title: str | None
    counts: dict[str, int] = field(default_factory=dict)

    def preview(self) -> dict[str, Any]:
        c = self.counts
        return {
            "affected": len(self.sources),
            "children": c.get("children", 0),
            "overrides_kept": c.get("classifications_kept", 0),
            "will_create": [f"item “{self.new_title}”"] if self.target is None else [],
            "will_delete": [f"{s.key} (merged into {self.target.key if self.target else self.new_title})" for s in self.sources],
            "sample": [
                {"minutes_moved": c.get("minutes", 0), "activities": c.get("activities", 0), "evidence": c.get("evidence", 0),
                 "comments": c.get("comments", 0), "tags": c.get("tags", 0), "links": c.get("links", 0)}
            ],
        }  # fmt: skip


async def _counts(uow: UnitOfWork, sources: list[m.WorkItem], target_id: str | None) -> dict[str, int]:
    s = uow.session
    ids = [x.id for x in sources]

    async def n(model: Any, col: Any, extra: Any = None) -> int:
        q = select(func.count()).select_from(model).where(col.in_(ids))
        if extra is not None:
            q = q.where(extra)
        return int(await s.scalar(q) or 0)

    minutes = int(
        await s.scalar(
            select(func.coalesce(func.sum(m.TimeEntry.minutes), 0)).where(m.TimeEntry.work_item_id.in_(ids))
        )
        or 0
    )
    return {
        "activities": await n(m.Activity, m.Activity.work_item_id),
        "minutes": minutes,
        "evidence": await n(m.Evidence, m.Evidence.work_item_id),
        "comments": await n(m.Comment, m.Comment.target_id, m.Comment.target_type == TargetType.WORK_ITEM),
        "tags": await n(m.TagLink, m.TagLink.target_id, m.TagLink.target_type == TargetType.WORK_ITEM),
        "links": int(
            await s.scalar(
                select(func.count())
                .select_from(m.WorkItemLink)
                .where(m.WorkItemLink.from_item_id.in_(ids) | m.WorkItemLink.to_item_id.in_(ids))
            )
            or 0
        ),
        "children": int(
            await s.scalar(select(func.count()).select_from(m.WorkItem).where(m.WorkItem.parent_id.in_(ids)))
            or 0
        ),
        "classifications_kept": 0,
    }


async def plan(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    sources: list[m.WorkItem],
    target: m.WorkItem | None,
    new_title: str | None,
    allow_cross_project: bool = False,
) -> MergePlan:
    """Work out what merging `sources` into `target` would move, without writing anything."""
    uniq = {x.id: x for x in sources}
    sources = list(uniq.values())
    if target is not None:
        sources = [x for x in sources if x.id != target.id]
    if not sources:
        raise Invalid(
            "nothing_to_merge", "name at least one item other than the target", pointer="/args/sources"
        )
    if target is None and not (new_title or "").strip():
        raise Invalid(
            "invalid", "give a target key or a title for the merged item", pointer="/args/new_title"
        )
    projects = {x.project_id for x in sources} | ({target.project_id} if target else set())
    if len(projects) > 1 and not allow_cross_project:
        raise Invalid(
            "cross_project",
            "these items are in different projects; pass allow_cross_project to merge them anyway",
            pointer="/args/sources",
        )
    for x in [*sources, *([target] if target else [])]:
        if x.deleted_at is not None:
            raise NotFound("not_found", f"{x.key} is already deleted or merged", pointer="/args/sources")
    p = MergePlan(sources, target, new_title)
    p.counts = await _counts(uow, sources, target.id if target else None)
    return p


async def apply(
    uow: UnitOfWork, ws: m.Workspace, p: MergePlan, *, actor_id: str, create_target: Any = None
) -> m.WorkItem:
    """Execute a plan. `create_target(title, template_item)` creates the new target when `p.target` is None."""
    s = uow.session
    target = p.target
    if target is None:
        assert create_target is not None  # noqa: S101
        target = await create_target(p.new_title, p.sources[0])
    sid = [x.id for x in p.sources]
    moved: dict[str, int] = {}

    # capture rows: same rows, new parent (content and identity untouched)
    for key, model in (("activities", m.Activity), ("time", m.TimeEntry), ("evidence", m.Evidence)):
        res = await s.execute(update(model).where(model.work_item_id.in_(sid)).values(work_item_id=target.id))
        moved[key] = rowcount(res)
    for model2 in (m.Comment, m.Attachment):
        await s.execute(
            update(model2)
            .where(model2.target_type == TargetType.WORK_ITEM, model2.target_id.in_(sid))
            .values(target_id=target.id)
        )
    await s.execute(update(m.Estimate).where(m.Estimate.work_item_id.in_(sid)).values(work_item_id=target.id))
    await s.execute(
        update(m.Checklist).where(m.Checklist.work_item_id.in_(sid)).values(work_item_id=target.id)
    )

    # tags: keep one link per tag
    have = set(
        (
            await s.scalars(
                select(m.TagLink.tag_id).where(
                    m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id == target.id
                )
            )
        ).all()
    )
    for link in (
        await s.scalars(
            select(m.TagLink).where(
                m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id.in_(sid)
            )
        )
    ).all():
        if link.tag_id in have:
            link.deleted_at = utcnow()
        else:
            link.target_id = target.id
            have.add(link.tag_id)

    # links: re-point, never self-loops or duplicates
    existing = {
        (x.from_item_id, x.to_item_id, x.link_type)
        for x in (
            await s.scalars(
                select(m.WorkItemLink).where(
                    or_(m.WorkItemLink.from_item_id == target.id, m.WorkItemLink.to_item_id == target.id)
                )
            )
        ).all()
    }
    for lk in (
        await s.scalars(
            select(m.WorkItemLink).where(
                m.WorkItemLink.from_item_id.in_(sid) | m.WorkItemLink.to_item_id.in_(sid)
            )
        )
    ).all():
        a = target.id if lk.from_item_id in sid else lk.from_item_id
        b = target.id if lk.to_item_id in sid else lk.to_item_id
        if a == b or (a, b, lk.link_type) in existing:
            await s.delete(lk)
            continue
        lk.from_item_id, lk.to_item_id = a, b
        existing.add((a, b, lk.link_type))

    # assignments and risk links: add what the target lacks
    have_asg = {
        (x.person_id, x.role)
        for x in (
            await s.scalars(
                select(m.Assignment).where(
                    m.Assignment.work_item_id == target.id, m.Assignment.unassigned_at.is_(None)
                )
            )
        ).all()
    }
    for a in (await s.scalars(select(m.Assignment).where(m.Assignment.work_item_id.in_(sid)))).all():
        if (a.person_id, a.role) in have_asg or a.unassigned_at is not None:
            continue
        a.work_item_id = target.id
        have_asg.add((a.person_id, a.role))
    have_risk = set(
        (await s.scalars(select(m.RiskLink.risk_id).where(m.RiskLink.work_item_id == target.id))).all()
    )
    for rl in (await s.scalars(select(m.RiskLink).where(m.RiskLink.work_item_id.in_(sid)))).all():
        if rl.risk_id in have_risk:
            await s.delete(rl)
        else:
            rl.work_item_id = target.id
            have_risk.add(rl.risk_id)

    # classifications: manual beats inherited; otherwise the target's own row stays
    mine = {
        (c.dimension_id, c.value_id): c
        for c in (
            await s.scalars(
                select(m.Classification).where(
                    m.Classification.target_type == TargetType.WORK_ITEM,
                    m.Classification.target_id == target.id,
                    m.Classification.deleted_at.is_(None),
                )
            )
        ).all()
    }
    kept = 0
    for c in (
        await s.scalars(
            select(m.Classification).where(
                m.Classification.target_type == TargetType.WORK_ITEM,
                m.Classification.target_id.in_(sid),
                m.Classification.deleted_at.is_(None),
            )
        )
    ).all():
        twin = mine.get((c.dimension_id, c.value_id))
        if twin is None:
            c.target_id = target.id
            mine[(c.dimension_id, c.value_id)] = c
        elif c.source == ValueSource.MANUAL and twin.source != ValueSource.MANUAL:
            twin.deleted_at = utcnow()
            c.target_id = target.id
            mine[(c.dimension_id, c.value_id)] = c
        else:
            c.deleted_at = utcnow()
            kept += 1

    # children move up to the target; sources are retired
    await s.execute(update(m.WorkItem).where(m.WorkItem.parent_id.in_(sid)).values(parent_id=target.id))
    for src in p.sources:
        src.deleted_at = utcnow()
        src.external_refs = {**(src.external_refs or {}), "merged_into": target.key}
        emit(
            uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=src.id,
            event_type="item.merged", payload={"before": {"key": src.key}, "after": {"merged_into": target.key}},
            source=EventSource.OVERLAY,
        )  # fmt: skip
    emit(
        uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.WORK_ITEM, entity_id=target.id,
        event_type="item.merged",
        payload={"after": {"key": target.key, "absorbed": [x.key for x in p.sources], "moved": moved, "classifications_kept": kept}},
        source=EventSource.OVERLAY,
    )  # fmt: skip
    await s.flush()
    return target
