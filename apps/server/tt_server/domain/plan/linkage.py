"""Plan ↔ execution linkage (plan P3 §3.11): a captured task attaches to the manager's deliverable, or asks.

Resolution order: LLM hint `parent` (handled by the capture hints) → glossary phrase → trigram ≥ 0.5 against deliverable titles →
otherwise a `ReviewItem{kind=unlinked_task}` carrying the top-3 deliverables. Progress then rolls up from the children.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import EventSource, ItemTypeKey, ProgressMode, StateCategory, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.domain.items import service as items

TRIGRAM_MIN = Decimal("0.5")


@dataclass(frozen=True)
class Candidate:
    item: m.WorkItem
    score: float


async def deliverables(session: AsyncSession, project_id: str) -> list[m.WorkItem]:
    return list(
        (
            await session.scalars(
                select(m.WorkItem)
                .join(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
                .where(
                    m.WorkItem.project_id == project_id,
                    m.WorkItem.deleted_at.is_(None),
                    m.WorkItemType.key == ItemTypeKey.DELIVERABLE,
                )
            )
        ).all()
    )


def _score(title: str) -> Any:
    """Best of: whole-string similarity, the task title inside the deliverable's, and the deliverable's title inside the task's."""
    return func.greatest(
        func.similarity(m.WorkItem.title, title),
        func.word_similarity(title, m.WorkItem.title),
        func.word_similarity(m.WorkItem.title, title),
    )


async def rank(session: AsyncSession, project_id: str, title: str, *, limit: int = 3) -> list[Candidate]:
    """Deliverables ordered by trigram score against `title` (see `_score`)."""
    score = _score(title)
    rows = (
        await session.execute(
            select(m.WorkItem, score)
            .join(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
            .where(
                m.WorkItem.project_id == project_id,
                m.WorkItem.deleted_at.is_(None),
                m.WorkItemType.key == ItemTypeKey.DELIVERABLE,
            )
            .order_by(score.desc(), m.WorkItem.key)
            .limit(limit)
        )
    ).all()
    return [Candidate(it, float(sc or 0)) for it, sc in rows]


async def glossary_match(
    session: AsyncSession, ws_id: str, project_id: str, text: str, ids: set[str]
) -> m.WorkItem | None:
    """A learned or manual phrase that names a deliverable of this project and occurs in the task text."""
    low = text.lower()
    entries = (
        await session.scalars(
            select(m.GlossaryEntry).where(
                m.GlossaryEntry.workspace_id == ws_id,
                m.GlossaryEntry.resolves_to_type == "item",
                m.GlossaryEntry.resolves_to_id.in_(ids or {""}),
                m.GlossaryEntry.deleted_at.is_(None),
            )
        )
    ).all()
    hit = next((e for e in sorted(entries, key=lambda e: -len(e.phrase)) if e.phrase.lower() in low), None)
    return await session.get(m.WorkItem, hit.resolves_to_id) if hit else None


async def attach_or_ask(ctx: Any, item: m.WorkItem, project: m.Project) -> str:
    """Called for a task the capture pipeline just created. Returns `linked:<key>`, `asked`, or `no_plan`."""

    s = ctx.session
    if item.parent_id is not None:
        return "has_parent"
    t = await s.get(m.WorkItemType, item.type_id)
    if t is not None and t.key in (ItemTypeKey.PHASE, ItemTypeKey.DELIVERABLE, ItemTypeKey.EPIC):
        return "container"
    delivs = await deliverables(s, project.id)
    if not delivs:
        return "no_plan"
    text = f"{item.title} {ctx.msg.raw_input or ''}"
    parent = await glossary_match(s, ctx.ws.id, project.id, text, {d.id for d in delivs})
    method, score = "glossary", 1.0
    ranked = await rank(s, project.id, item.title)
    if parent is None and ranked and Decimal(str(ranked[0].score)) >= TRIGRAM_MIN:
        gap_ok = len(ranked) < 2 or ranked[0].score - ranked[1].score >= 0.05 or ranked[0].score >= 0.7
        if gap_ok:
            parent, method, score = ranked[0].item, "trigram", ranked[0].score
    if parent is not None:
        try:
            await items.validate_nesting(s, ctx.ws, item.type_id, parent)
        except Exception:
            parent = None
    if parent is not None:
        await items.move_item(s, item, parent)
        ctx.emit(
            "item.parent_changed", TargetType.WORK_ITEM, item.id,
            {"after": {"parent_id": parent.id, "deliverable": parent.key}, "resolution": {"method": method, "score": round(score, 3)}},
        )  # fmt: skip
        await refresh_ancestors(s, item)
        return f"linked:{parent.key}"
    sug = [{"key": c.item.key, "title": c.item.title, "score": round(c.score, 3)} for c in ranked]
    review_id = new_id()
    ctx.review_ids.append(review_id)
    s.add(
        m.ReviewItem(
            id=review_id, workspace_id=ctx.ws.id, kind="unlinked_task", target_type=TargetType.WORK_ITEM.value, target_id=item.id,
            suggestion={"deliverables": sug, "project": project.key}, confidence=Decimal(str(round(ranked[0].score, 4))) if ranked else None,
        )
    )  # fmt: skip
    ctx.emit(
        "review.created", TargetType.WORK_ITEM, item.id, {"after": {"kind": "unlinked_task", "top": sug[:1]}}
    )
    return "asked"


# ---------------------------------------------------------------- progress roll-up (progress_mode = children)
async def refresh_ancestors(session: AsyncSession, item: m.WorkItem) -> None:
    """Recompute `progress_pct` on every ancestor whose mode is `children` (phase / deliverable by default)."""
    pid = item.parent_id
    seen = 0
    while pid and seen < 8:
        parent = await session.get(m.WorkItem, pid)
        if parent is None:
            return
        if parent.progress_mode == ProgressMode.CHILDREN:
            kids = (
                await session.execute(
                    select(m.WorkflowState.category)
                    .join(m.WorkItem, m.WorkItem.state_id == m.WorkflowState.id)
                    .where(m.WorkItem.parent_id == parent.id, m.WorkItem.deleted_at.is_(None))
                )
            ).all()
            if kids:
                done = sum(1 for (c,) in kids if c == StateCategory.DONE)
                parent.progress_pct = round(done * 100 / len(kids))
        pid, seen = parent.parent_id, seen + 1


async def _children_progress(session: AsyncSession, parent: m.WorkItem) -> int | None:
    kids = (
        await session.execute(
            select(m.WorkflowState.category)
            .join(m.WorkItem, m.WorkItem.state_id == m.WorkflowState.id)
            .where(m.WorkItem.parent_id == parent.id, m.WorkItem.deleted_at.is_(None))
        )
    ).all()
    return round(sum(1 for (c,) in kids if c == StateCategory.DONE) * 100 / len(kids)) if kids else None


async def recompute_all(factory: Any) -> int:
    """Nightly heal: re-derive every `children`-mode container, deepest first so a phase sees its deliverables' final value.
    Each correction is an event (`item.progress_recomputed`), so drift is visible rather than silently repaired."""

    fixed = 0
    async with factory() as s:
        ids = (
            await s.execute(
                select(m.WorkItem.id, m.Project.workspace_id)
                .join(m.Project, m.Project.id == m.WorkItem.project_id)
                .where(m.WorkItem.progress_mode == ProgressMode.CHILDREN, m.WorkItem.deleted_at.is_(None))
                .order_by(func.nlevel(m.WorkItem.path).desc())
            )
        ).all()
    for item_id, ws_id in ids:
        async with UnitOfWork(factory) as uow:
            it = await uow.session.get(m.WorkItem, item_id)
            if it is None:
                continue
            want = await _children_progress(uow.session, it)
            if want is None or want == it.progress_pct:
                continue
            before, it.progress_pct = it.progress_pct, want
            emit(
                uow, workspace_id=ws_id, actor_id=it.reporter_id, entity_type=TargetType.WORK_ITEM, entity_id=it.id,
                event_type="item.progress_recomputed", payload={"before": {"progress_pct": before}, "after": {"progress_pct": want}},
                source=EventSource.RULE,
            )  # fmt: skip
            await uow.commit()
            fixed += 1
    return fixed


async def on_state_changed(uow: UnitOfWork, item: m.WorkItem, old: StateCategory, new: StateCategory) -> None:
    """Registered on the P1 extension point: a task finishing moves its deliverable's and phase's progress."""
    if StateCategory.DONE in (old, new):
        await uow.session.flush()
        await refresh_ancestors(uow.session, item)


items_hook_registered = False


def register() -> None:
    global items_hook_registered

    if not items_hook_registered:
        items.on_state_category_changed.append(on_state_changed)
        items_hook_registered = True


# ---------------------------------------------------------------- schedule changes: slip against the baseline, broken chains
SLIP_DAYS_DEFAULT = 7


async def plan_changed(uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem) -> list[str]:
    """After a deliverable's dates moved: (1) a `plan_slip` review row when it is later than its baseline by more than the
    workspace's `plan_slip_days`; (2) successors whose `finish_to_start` (+ lag) no longer fits get a `plan_conflict` row.
    Both are idempotent (one open row per item and kind). Returns the kinds raised."""
    from datetime import timedelta

    from tt_schema.enums import LinkType

    s = uow.session
    t = await s.get(m.WorkItemType, item.type_id)
    if t is None or t.key != ItemTypeKey.DELIVERABLE or item.planned_end is None:
        return []
    raised: list[str] = []

    async def raise_once(kind: str, suggestion: dict[str, Any], target: m.WorkItem) -> None:
        if await s.scalar(
            select(m.ReviewItem.id).where(
                m.ReviewItem.workspace_id == ws.id,
                m.ReviewItem.kind == kind,
                m.ReviewItem.status == "open",
                m.ReviewItem.target_id == target.id,
            )
        ):
            return
        rid = new_id()
        s.add(
            m.ReviewItem(
                id=rid,
                workspace_id=ws.id,
                kind=kind,
                target_type=TargetType.WORK_ITEM.value,
                target_id=target.id,
                suggestion=suggestion,
            )
        )

        sysp = await system_person(s)
        from tt_schema.enums import EventSource

        emit(
            uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.WORK_ITEM, entity_id=target.id, event_type="review.created",
            payload={"after": {"review_item_id": rid, "kind": kind, **suggestion}}, source=EventSource.RULE,
        )  # fmt: skip
        raised.append(kind)

    threshold = int((ws.settings or {}).get("plan_slip_days", SLIP_DAYS_DEFAULT))
    base = await s.scalar(
        select(m.Baseline.planned_end)
        .where(m.Baseline.work_item_id == item.id)
        .order_by(m.Baseline.captured_at.desc())
        .limit(1)
    )
    if base is not None and (item.planned_end - base).days > threshold:
        await raise_once(
            "plan_slip",
            {"item": item.key, "days": (item.planned_end - base).days, "baseline_end": base.isoformat()},
            item,
        )
    succ = (
        await s.execute(
            select(m.WorkItem, m.WorkItemLink.lag_days)
            .join(m.WorkItemLink, m.WorkItemLink.to_item_id == m.WorkItem.id)
            .where(
                m.WorkItemLink.from_item_id == item.id, m.WorkItemLink.link_type == LinkType.FINISH_TO_START
            )
        )
    ).all()
    for nxt, lag in succ:
        earliest = item.planned_end + timedelta(days=lag or 0)
        if nxt.planned_start is not None and nxt.planned_start < earliest:
            await raise_once(
                "plan_conflict",
                {"predecessor": item.key, "successor": nxt.key, "earliest_start": earliest.isoformat()},
                nxt,
            )
    return raised


register()  # importing this module is enough: the hook is on the P1 extension point
