"""Glue between the engine and the outside: run a policy now (sweep), react to one event, the nightly pass."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import tt_cel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import utcnow
from tt_schema.enums import EventSource, PolicyTrigger, TargetType

from tt_server.clock import ws_today
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.domain.overlay import process as proc
from tt_server.governance import apply as applymod
from tt_server.governance import context as ctxmod
from tt_server.governance import engine, habits, metrics, service
from tt_server.governance import facts as factmod


async def run_policy(
    factory: async_sessionmaker[AsyncSession], policy_id: str, *, now: datetime | None = None
) -> applymod.Applied | None:
    """Full sweep of one policy in its own unit of work."""
    now = now or utcnow()
    async with UnitOfWork(factory) as uow:
        policy = await uow.session.get(m.Policy, policy_id)
        if policy is None or not policy.is_active or policy.deleted_at is not None:
            return None
        ws = await uow.session.get(m.Workspace, policy.workspace_id)
        assert ws is not None  # noqa: S101
        res = await engine.evaluate(uow.session, ws, policy, now=now)
        out = await applymod.apply(uow, ws, policy, res, now=now)
        await uow.commit()
        return out


async def run_workspace(
    factory: async_sessionmaker[AsyncSession],
    ws_id: str,
    *,
    now: datetime | None = None,
    only_due: bool = False,
) -> int:
    """Sweep every active scheduled policy of the workspace (`only_due`: just the ones whose cron matches this minute)."""
    now = now or utcnow()
    async with factory() as s:
        policies = (
            await s.scalars(
                select(m.Policy).where(
                    m.Policy.workspace_id == ws_id,
                    m.Policy.is_active.is_(True),
                    m.Policy.trigger == PolicyTrigger.SCHEDULED,
                    m.Policy.deleted_at.is_(None),
                )
            )
        ).all()
        ids = [p.id for p in policies if not only_due or service.due_now(p, now)]
    n = 0
    for pid in ids:
        if await run_policy(factory, pid, now=now) is not None:
            n += 1
    return n


def _refs_for_event(ev: m.Event, target: TargetType, activity_item: dict[str, str]) -> list[engine.Ref]:
    et = ev.entity_type
    if et == TargetType.WORK_ITEM:
        return [engine.Ref(TargetType.WORK_ITEM, ev.entity_id, item_id=ev.entity_id)]
    if et == TargetType.ACTIVITY:
        return [engine.Ref(TargetType.ACTIVITY, ev.entity_id, item_id=activity_item.get(ev.entity_id))]
    if et == TargetType.DAILY_LOG:
        return [engine.Ref(TargetType.DAILY_LOG, ev.entity_id)]
    return []


async def on_event(factory: async_sessionmaker[AsyncSession], event_id: str) -> int:
    """Evaluate the active on_event policies that listen to this event type for the entity it concerns, then the stage rules."""
    async with UnitOfWork(factory) as uow:
        s = uow.session
        ev = await s.get(m.Event, event_id)
        if ev is None:
            return 0
        ws = await s.get(m.Workspace, ev.workspace_id)
        assert ws is not None  # noqa: S101
        await service.ensure_system(uow)
        policies = [
            p
            for p in (
                await s.scalars(
                    select(m.Policy).where(
                        m.Policy.workspace_id == ws.id,
                        m.Policy.is_active.is_(True),
                        m.Policy.trigger == PolicyTrigger.ON_EVENT,
                        m.Policy.deleted_at.is_(None),
                    )
                )
            ).all()
            if _listens(p, ev.event_type)
        ]
        n = 0
        act_item: dict[str, str] = {}
        if ev.entity_type == TargetType.ACTIVITY:
            a = await s.get(m.Activity, ev.entity_id)
            if a is not None:
                act_item[a.id] = a.work_item_id
        item_id = ev.entity_id if ev.entity_type == TargetType.WORK_ITEM else act_item.get(ev.entity_id)
        for p in policies:
            refs = _refs_for_event(ev, p.target, act_item)
            if p.target in (TargetType.WORK_ITEM, TargetType.STAGE_PLACEMENT) and item_id:
                refs = [engine.Ref(TargetType.WORK_ITEM, item_id, item_id=item_id)]
            if not refs and p.target not in (TargetType.WORK_ITEM, TargetType.STAGE_PLACEMENT):
                continue
            res = (
                await engine.evaluate(s, ws, p, now=ev.occurred_at, event=ev, only=refs or None)
                if refs
                else None
            )
            if res is None:
                continue
            await applymod.apply(uow, ws, p, res, now=ev.occurred_at)
            n += 1
        if item_id and ev.source != EventSource.RULE:
            n += await stage_rules(uow, ws, item_id, ev)
        await uow.commit()
        return n


def _listens(p: m.Policy, event_type: str) -> bool:
    for pat in p.event_types or []:
        if (
            pat == event_type
            or (pat.endswith(".*") and event_type.startswith(pat[:-1]))
            or (pat.endswith("*") and event_type.startswith(pat[:-1]))
        ):
            return True
    return False


async def stage_rules(uow: UnitOfWork, ws: m.Workspace, item_id: str, ev: m.Event) -> int:
    """`StageTransitionRule.auto_when` (CEL over the same context): move the item when the rule holds, as `source=rule`."""

    s = uow.session
    place = await s.scalar(
        select(m.StagePlacement)
        .where(
            m.StagePlacement.target_type == TargetType.WORK_ITEM,
            m.StagePlacement.target_id == item_id,
            m.StagePlacement.exited_at.is_(None),
        )
        .order_by(m.StagePlacement.entered_at.desc())
    )
    if place is None:
        return 0
    rules = (
        await s.scalars(
            select(m.StageTransitionRule).where(
                m.StageTransitionRule.process_id == place.process_id,
                m.StageTransitionRule.auto_when.is_not(None),
                m.StageTransitionRule.deleted_at.is_(None),
            )
        )
    ).all()
    rules = [r for r in rules if r.from_stage_id in (None, place.stage_id)]
    if not rules:
        return 0
    items = await ctxmod.load_items(s, ws.id, [item_id])
    it = items.get(item_id)
    if it is None:
        return 0
    now = ev.occurred_at
    fx = await factmod.load(
        s,
        ws.id,
        {"evidence", "placement", "violations", "children"},
        now=now,
        item_ids=[item_id],
        person_ids=[],
        item_rows=items,
        windows=set(),
        project_keys=[],
    )
    funcs = tt_cel.bind(fx)
    root = ctxmod.blank_context()
    root["item"] = {k: v for k, v in it.items() if not k.startswith("_")}
    root["stage"], root["placement"], root["event"] = it["stage"], it["placement"], ctxmod.event_root(ev)
    n = 0
    sysp = await system_person(s)
    for r in rules:
        if not r.auto_when:
            continue
        try:
            ok = tt_cel.compile(r.auto_when).evaluate(root, funcs, now=now)
        except tt_cel.CelError:
            continue
        if ok is True:
            process = await s.get(m.Process, r.process_id)
            stage = await s.get(m.Stage, r.to_stage_id)
            assert process is not None and stage is not None  # noqa: S101
            await proc.place(
                uow,
                ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item_id,
                process=process,
                stage=stage,
                moved_by=sysp.id,
                reason=f"auto_when: {r.auto_when[:120]}",
                source=EventSource.RULE,
                correlation_id=ev.id,
            )
            n += 1
            break
    return n


async def nightly(
    factory: async_sessionmaker[AsyncSession], ws_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Habits, metric snapshots (default weighting + every scheme) and key results for one workspace."""
    now = now or utcnow()
    async with UnitOfWork(factory) as uow:
        ws = await uow.session.get(m.Workspace, ws_id)
        assert ws is not None  # noqa: S101
        await service.ensure_system(uow)
        hs = await habits.compute(uow, ws, now=now)
        await uow.commit()
    rows = 0
    async with UnitOfWork(factory) as uow:
        ws = await uow.session.get(m.Workspace, ws_id)
        assert ws is not None  # noqa: S101
        day = ws_today(ws, now)
        schemes: list[str | None] = [
            None,
            *(
                await uow.session.scalars(
                    select(m.WeightScheme.id).where(
                        m.WeightScheme.workspace_id == ws_id, m.WeightScheme.deleted_at.is_(None)
                    )
                )
            ).all(),
        ]
        for sid in schemes:
            rows += await metrics.snapshot_day(uow, ws, day, scheme_id=sid)
        krs = await metrics.update_key_results(uow, ws)
        await uow.commit()
    return {"habits": hs.upserted, "flagged": hs.flagged, "snapshots": rows, "key_results": krs}


__all__ = ["emit", "nightly", "on_event", "run_policy", "run_workspace", "stage_rules"]
