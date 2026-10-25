"""LLM-written defaults (Phase 2b §2b.5): review-row suggestions and classification pre-fill.

The gateway proposes, this module validates against what exists in the workspace and stores the proposal as
*unconfirmed*: a suggestion on a `ReviewItem`, or a `Classification{is_confirmed=false, source=rule}`.
A person still presses the button.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.enums import DimensionKind, ProjectKind, StateCategory, TargetType, ValueSource

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay.review import KIND_ALIASES
from tt_server.integrations.llm import GatewayUnavailable, LLMGatewayClient

SUGGESTIBLE = {
    "unclassified_project",
    "unknown_person",
    "stage_suggestion",
    "product_suggestion",
    "deferred_stage",
    "deferred_product",
}
MIN_CONFIDENCE = 0.5  # below this a suggestion is not worth a button


@dataclass
class RunSummary:
    """Counters and errors of one suggestion run."""

    attempted: int = 0
    written: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


async def _candidates(s: AsyncSession, ws_id: str, kind: str) -> dict[str, list[str]]:
    if kind == "unclassified_project":
        rows = await s.scalars(
            select(m.Project.key).where(
                m.Project.workspace_id == ws_id,
                m.Project.kind != ProjectKind.INBOX,
                m.Project.deleted_at.is_(None),
            )
        )
        return {"projects": sorted(rows.all())}
    if kind == "unknown_person":
        rows = await s.scalars(
            select(m.WorkspaceMember.handle).where(m.WorkspaceMember.workspace_id == ws_id)
        )
        return {"people": sorted(rows.all())}
    if kind in ("stage_suggestion", "deferred_stage"):
        rows = await s.scalars(
            select(m.Stage.key)
            .join(m.Process, m.Process.id == m.Stage.process_id)
            .where(m.Process.workspace_id == ws_id)
        )
        return {"stages": sorted(set(rows.all()))}
    rows = await s.scalars(
        select(m.Product.key).where(m.Product.workspace_id == ws_id, m.Product.deleted_at.is_(None))
    )
    return {"products": sorted(rows.all())}


async def suggest_one(
    factory: async_sessionmaker[AsyncSession],
    client: LLMGatewayClient,
    ws_id: str,
    ws_slug: str,
    review_id: str,
) -> str:
    """Returns written | skipped | unavailable. One unit of work per row."""
    async with UnitOfWork(factory) as uow:
        row = await uow.session.get(m.ReviewItem, review_id)
        if row is None or row.workspace_id != ws_id or row.status != "open":
            return "skipped"
        kind = KIND_ALIASES.get(row.kind, row.kind)
        if row.kind not in SUGGESTIBLE and kind not in SUGGESTIBLE or (row.suggestion or {}).get("llm"):
            return "skipped"
        item = await uow.session.get(m.WorkItem, row.target_id)
        cands = await _candidates(uow.session, ws_id, row.kind if row.kind in SUGGESTIBLE else kind)
        payload = {
            "kind": row.kind if row.kind in SUGGESTIBLE else kind,
            "item_title": item.title if item else None,
            "heard": {
                k: v
                for k, v in (row.suggestion or {}).items()
                if k in {"phrase", "name", "handle", "role", "stage", "product"}
            },
        }
        try:
            ans = await client.suggest_review(ws_slug, payload, cands)
        except GatewayUnavailable:
            return "unavailable"
        sugg = (ans.output or {}).get("suggestions") or []
        if not sugg or sugg[0]["confidence"] < MIN_CONFIDENCE:
            row.suggestion = {**(row.suggestion or {}), "llm": {"none": True, "agent_run_id": ans.run_id}}
            await _commit_marker(uow, ws_id, row, ans.run_id, None)
            return "skipped"
        row.suggestion = {
            **(row.suggestion or {}),
            "llm": {"suggestions": sugg, "agent_run_id": ans.run_id, "model": ans.model},
        }
        row.confidence = sugg[0]["confidence"] if row.confidence is None else row.confidence
        await _commit_marker(uow, ws_id, row, ans.run_id, sugg[0]["confidence"])
        return "written"


async def _commit_marker(
    uow: UnitOfWork, ws_id: str, row: m.ReviewItem, run_id: str, confidence: float | None
) -> None:
    bot = await system_person(uow.session)
    uow.context["agent_run_id"] = run_id
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=bot.id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=row.target_id,
        event_type="agent.suggestion_written",
        payload={"after": {"review_item_id": row.id, "confidence": confidence}},
    )
    await uow.commit()


async def run_review_suggestions(
    factory: async_sessionmaker[AsyncSession], client: LLMGatewayClient, ws_id: str, limit: int = 25
) -> RunSummary:
    """Ask the LLM gateway for suggestions for open review rows of a workspace (at most `limit`) and store them."""
    out = RunSummary()
    todo: list[str] = []
    async with factory() as s:
        slug = await s.scalar(select(m.Workspace.slug).where(m.Workspace.id == ws_id))
        ids = (
            await s.scalars(
                select(m.ReviewItem.id)
                .where(
                    m.ReviewItem.workspace_id == ws_id,
                    m.ReviewItem.status == "open",
                    m.ReviewItem.deleted_at.is_(None),
                )
                .order_by(m.ReviewItem.created_at)
                .limit(limit * 4)
            )
        ).all()
        for i in ids:
            row = await s.get(m.ReviewItem, i)
            if row is not None and not (row.suggestion or {}).get("llm"):
                todo.append(i)
        todo = todo[:limit]
    for rid in todo:
        out.attempted += 1
        res = await suggest_one(factory, client, ws_id, slug or "", rid)
        if res == "written":
            out.written += 1
        elif res == "unavailable":
            out.errors.append("gateway unavailable")
            break
        else:
            out.skipped += 1
    return out


async def _prefill_plan(
    factory: async_sessionmaker[AsyncSession], ws_id: str, limit: int
) -> tuple[str, list[tuple[str, str, list[tuple[str, str]], list[tuple[str, str, str]]]]]:
    """(workspace slug, [(dimension id, key, values, items missing it)]) -- read in one short session."""
    plan: list[tuple[str, str, list[tuple[str, str]], list[tuple[str, str, str]]]] = []
    async with factory() as s:
        slug = await s.scalar(select(m.Workspace.slug).where(m.Workspace.id == ws_id))
        dims = (
            await s.scalars(
                select(m.Dimension).where(
                    m.Dimension.workspace_id == ws_id,
                    m.Dimension.kind == DimensionKind.CUSTOM,
                    m.Dimension.is_required_for.is_not(None),
                    m.Dimension.deleted_at.is_(None),
                )
            )
        ).all()
        for dim in dims:
            values = [
                (k, n)
                for k, n in (
                    await s.execute(
                        select(m.DimensionValue.key, m.DimensionValue.name).where(
                            m.DimensionValue.dimension_id == dim.id,
                            m.DimensionValue.is_active.is_(True),
                            m.DimensionValue.deleted_at.is_(None),
                        )
                    )
                ).all()
            ]
            classified = select(m.Classification.target_id).where(
                m.Classification.dimension_id == dim.id,
                m.Classification.target_type == TargetType.WORK_ITEM,
                m.Classification.deleted_at.is_(None),
            )
            items = [
                (i, k, t)
                for i, k, t in (
                    await s.execute(
                        select(m.WorkItem.id, m.WorkItem.key, m.WorkItem.title)
                        .join(m.Project, m.Project.id == m.WorkItem.project_id)
                        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
                        .where(
                            m.Project.workspace_id == ws_id,
                            m.Project.kind != ProjectKind.INBOX,
                            m.WorkItem.deleted_at.is_(None),
                            m.WorkflowState.category != StateCategory.DONE,
                            m.WorkItem.id.not_in(classified),
                        )
                        .order_by(m.WorkItem.created_at)
                        .limit(limit)
                    )
                ).all()
            ]
            if values and items:
                plan.append((dim.id, dim.key, values, items))
    return slug or "", plan


async def prefill_classifications(
    factory: async_sessionmaker[AsyncSession], client: LLMGatewayClient, ws_id: str, limit: int = 25
) -> RunSummary:
    """Items missing a value in a dimension flagged `is_required_for` get an unconfirmed guess."""
    out = RunSummary()
    slug, plan = await _prefill_plan(factory, ws_id, limit)
    for (
        dim_id,
        dim_key,
        values,
        items,
    ) in plan:  # no session is held open while the gateway thinks or we write
        out.attempted += len(items)
        try:
            ans = await client.suggest_classify(
                slug,
                [{"key": k, "title": t} for _, k, t in items],
                [{"key": dim_key, "values": [{"key": k, "name": n} for k, n in values]}],
            )
        except GatewayUnavailable as exc:
            out.errors.append(str(exc))
            return out
        by_key = {k: i for i, k, _ in items}
        for sug in (ans.output or {}).get("items", []):
            conf = float(sug.get("confidence") or 0)
            if sug["key"] not in by_key or conf < MIN_CONFIDENCE:
                out.skipped += 1
                continue
            if await _write_classification(
                factory, ws_id, dim_id, by_key[sug["key"]], sug["value"], conf, ans.run_id
            ):
                out.written += 1
    return out


async def _write_classification(
    factory: async_sessionmaker[AsyncSession],
    ws_id: str,
    dim_id: str,
    item_id: str,
    value_key: str,
    conf: float,
    run_id: str,
) -> bool:
    async with UnitOfWork(factory) as uow:
        ws = await uow.session.get(m.Workspace, ws_id)
        dim = await uow.session.get(m.Dimension, dim_id)
        value = await uow.session.scalar(
            select(m.DimensionValue).where(
                m.DimensionValue.dimension_id == dim_id, m.DimensionValue.key == value_key
            )
        )
        if ws is None or dim is None or value is None:
            return False
        bot = await system_person(uow.session)
        uow.context["actor_id"] = bot.id
        uow.context["agent_run_id"] = run_id
        row = await classify_svc.classify(
            uow, ws, target_type=TargetType.WORK_ITEM, target_id=item_id, dimension=dim, value=value,
            source=ValueSource.RULE, set_by=bot.id, confidence=conf, is_confirmed=False,
        )  # fmt: skip
        if row is None:
            return False
        emit(
            uow,
            workspace_id=ws_id,
            actor_id=bot.id,
            entity_type=TargetType.WORK_ITEM,
            entity_id=item_id,
            event_type="agent.suggestion_written",
            payload={"after": {"dimension": dim.key, "value": value.key, "confidence": conf}},
        )
        await uow.commit()
        return True
