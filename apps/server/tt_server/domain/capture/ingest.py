"""Capture ingestion pipeline (Phase 1 §1.4): ClientBatch -> items, activities, time, evidence, events.

Capture never blocks: every unresolvable reference degrades to the Inbox, `is_provisional`, a `ReviewItem`
or a warning. The only rejection is a schema violation, handled at the API layer (422).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id, utcnow
from tt_schema.client import ClientAction, ClientBatch, ClientMessage
from tt_schema.enums import (
    TargetType,
    Visibility,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.capture import dailylog
from tt_server.domain.capture.ingest_actions import apply_action, apply_fields, current_activity
from tt_server.domain.capture.ingest_context import (
    CONFIDENCE_FLOOR,
    MATCH_GAP,
    MATCH_THRESHOLD,
    RECENCY_BONUS,
    RECENCY_DAYS,
    Ctx,
    log,
)
from tt_server.domain.capture.ingest_hints import apply_hints
from tt_server.domain.capture.ingest_resolve import (
    DEFAULT_DEV_KEY,
    DEV_TAG,
    add_review,
    calendar_key,
    can_act_for,
    dev_project_key,
    ensure_dev_project,
    is_dev_project,
    member_by_handle,
    resolve_item,
    resolve_project,
    title_phrase,
)
from tt_server.domain.common import aware, zone
from tt_server.domain.items import service as items
from tt_server.domain.overlay import weights
from tt_server.domain.overlay.mirrors import sync_item_mirrors
from tt_server.domain.plan import linkage
from tt_server.integrations.jira import sync as jira_sync
from tt_server.settings import get_settings

# ---------------------------------------------------------------- one message


@dataclass
class MessageResult:
    """Outcome of ingesting one client message: status, item, warnings and review items."""

    client_id: str
    status: str
    item: dict[str, Any] | None = None
    activity_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    review_item_ids: list[str] = field(default_factory=list)
    error_code: str | None = None
    resolution: dict[str, Any] = field(default_factory=dict)
    jira: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "status": self.status,
            "item": self.item,
            "activity_id": self.activity_id,
            "warnings": self.warnings,
            "review_item_ids": self.review_item_ids,
            "error_code": self.error_code,
            "resolution": self.resolution,
            "jira": self.jira,
        }


async def _unlog(ctx: Ctx) -> MessageResult:
    """`time.unlog`: soft-delete the time entry a person's earlier message created (Phase 15 "Unsend"). Only its owner may."""
    msg = ctx.msg
    te = await ctx.session.scalar(
        select(m.TimeEntry).where(
            m.TimeEntry.workspace_id == ctx.ws.id,
            m.TimeEntry.client_id == msg.corrects,
            m.TimeEntry.deleted_at.is_(None),
        )
    )
    if (
        te is None or te.person_id != ctx.actor.person_id
    ):  # not yours behaves as if it did not exist (ADR-0008)
        ctx.warnings.append("unlog_target_not_found")
        ctx.emit(
            "time.unlog_ignored",
            TargetType.PERSON,
            ctx.actor.person_id,
            {"after": {"corrects": msg.corrects}},
        )
        status, item = "applied_with_warnings", None
    else:
        te.deleted_at = utcnow()
        wi = await ctx.session.get(m.WorkItem, te.work_item_id)
        ctx.emit(
            "time.unlogged",
            TargetType.WORK_ITEM,
            te.work_item_id,
            {
                "before": {"time_entry_id": te.id, "minutes": te.minutes, "date": te.date.isoformat()},
                "corrects": msg.corrects,
            },
        )
        status = "applied"
        item = {"id": wi.id, "key": wi.key, "title": wi.title} if wi else None
    await ctx.session.flush()
    result = MessageResult(
        msg.client_id, status, item=item, warnings=ctx.warnings, resolution={"unlog": True}
    )
    primary = ctx.events[0]
    primary.client_id = msg.client_id
    primary.payload = {**primary.payload, "result": result.as_dict()}
    return result


async def _process(
    uow: UnitOfWork,
    ws: m.Workspace,
    msg: ClientMessage,
    client_version: str,
    correlation_id: str,
    mode: str | None = None,
) -> MessageResult:
    session = uow.session
    dup = await session.scalar(
        select(m.Event).where(m.Event.client_id == msg.client_id, m.Event.workspace_id == ws.id)
    )
    if dup is not None:
        stored = dict((dup.payload or {}).get("result") or {})
        stored.update(client_id=msg.client_id, status="deduplicated", deduplicated=True)
        return MessageResult(**{k: v for k, v in stored.items() if k in MessageResult.__dataclass_fields__})
    actor = await member_by_handle(session, ws.id, msg.actor)
    if actor is None:
        return MessageResult(msg.client_id, "failed", error_code="unknown_actor")
    actor_person = await session.get(m.Person, actor.person_id)
    subject, warnings = actor, []
    if msg.on_behalf_of:
        target = await member_by_handle(session, ws.id, msg.on_behalf_of)
        if target and await can_act_for(session, ws.id, actor.person_id, target.person_id):
            subject = target
        else:
            warnings.append("on_behalf_of_ignored")
    subject_person = await session.get(m.Person, subject.person_id)
    assert actor_person and subject_person  # noqa: S101
    ctx = Ctx(
        uow=uow,
        ws=ws,
        msg=msg,
        client_version=client_version,
        correlation_id=correlation_id,
        actor=actor,
        subject=subject,
        actor_person=actor_person,
        subject_person=subject_person,
        at=aware(msg.at),
        warnings=warnings,
    )
    uow.context.update(actor_id=actor.person_id, correlation_id=correlation_id)

    if msg.corrects and msg.action == ClientAction.UPDATE:
        return await _unlog(ctx)
    ckey = calendar_key(msg)
    if (
        ckey and msg.minutes and msg.minutes > 0
    ):  # the same calendar occurrence is never logged twice (ADR-0054)
        existing = await session.scalar(
            select(m.TimeEntry).where(
                m.TimeEntry.workspace_id == ws.id,
                m.TimeEntry.person_id == subject.person_id,
                m.TimeEntry.calendar_provider == ckey[0],
                m.TimeEntry.calendar_event_id == ckey[1],
                m.TimeEntry.calendar_instance_start == ckey[2],
                m.TimeEntry.deleted_at.is_(None),
            )
        )
        if existing is not None:
            prior = await session.get(m.WorkItem, existing.work_item_id)
            return MessageResult(
                msg.client_id,
                "deduplicated",
                item={"id": prior.id, "key": prior.key, "title": prior.title} if prior else None,
                resolution={"dedup": "calendar_ref", "time_entry_id": existing.id},
            )

    project = await resolve_project(ctx)
    dev_project = is_dev_project(project)
    if dev_project and DEV_TAG not in [t.lower().lstrip("#") for t in msg.tags]:
        msg.tags = [*msg.tags, DEV_TAG]
    item, info = await resolve_item(ctx, project)
    created = item is None
    if (
        item is None
        and msg.item is None
        and msg.action
        in {
            ClientAction.PAUSE,
            ClientAction.RESUME,
            ClientAction.DONE,
            ClientAction.NOTE,
            ClientAction.EVIDENCE,
            ClientAction.LOG_TIME,
        }
    ):
        current = await current_activity(ctx, None)
        if current:
            item = await session.get(m.WorkItem, current.work_item_id)
            info = {"method": "matched", "by": "current_activity", "score": 1.0}
            created = False
    if item is None:
        title = (
            (msg.item.title if msg.item and msg.item.title else None)
            or msg.note
            or msg.raw_input
            or "Untitled"
        )
        team = await session.scalar(
            select(m.Membership.team_id)
            .where(m.Membership.person_id == subject.person_id, m.Membership.left_at.is_(None))
            .order_by(m.Membership.joined_at)
        )
        item_fields: dict[str, Any] = {"visibility": Visibility.PRIVATE} if dev_project else {}  # ADR-0055
        item = await items.create_item(
            uow,
            ws,
            project,
            title=title[:500],
            reporter_id=actor.person_id,
            assignee_id=subject.person_id,
            team_id=team,
            provisional=project.kind.value == "inbox",
            source={"client": client_version, "raw": msg.raw_input},
            **item_fields,
        )
        ctx.emit(
            "item.created",
            TargetType.WORK_ITEM,
            item.id,
            {"after": {"key": item.key, "title": item.title, "project": project.key}},
        )
    else:
        ctx.emit(
            "item.matched", TargetType.WORK_ITEM, item.id, {"after": {"key": item.key}, "resolution": info}
        )
    ctx.resolution["item"] = info

    activity = await apply_action(ctx, item, created)
    await apply_fields(ctx, item, activity)
    if created and (ctx.resolution["project"].get("unresolved") or project.kind.value == "inbox"):
        # every item born in an Inbox gets a row, even with no phrase to learn from (UX 3, Phase 13 R6.1)
        await add_review(
            ctx,
            "unclassified_project",
            item.id,
            {"phrase": ctx.resolution["project"].get("unresolved") or title_phrase(item.title)},
        )
    await apply_hints(ctx, item, project)
    if created and project.kind.value != "inbox":
        ctx.resolution["plan_link"] = await linkage.attach_or_ask(ctx, item, project)
    await sync_item_mirrors(uow, ws, item, set_by=actor.person_id)
    await weights.recompute_item(uow, ws, item, actor_id=actor.person_id)
    await session.flush()
    if any(e.event_type in ("time.logged", "activity.ended") for e in ctx.events):
        # the person-day has work now: keep its DailyLog draft current without waiting for the nightly job or a GET
        day = aware(ctx.at).astimezone(zone(subject_person.time_zone)).date()
        await dailylog.build_for_person(uow, ws, subject, day, actor.person_id)

    result = MessageResult(
        msg.client_id,
        "applied_with_warnings" if ctx.warnings else "applied",
        item={"id": item.id, "key": item.key, "title": item.title, "project_key": project.key},
        activity_id=activity.id if activity else None,
        warnings=ctx.warnings,
        review_item_ids=ctx.review_ids,
        resolution=ctx.resolution,
    )
    extra: dict[str, Any] = {}
    if mode:
        extra["mode"] = mode
    if msg.jira:  # the push itself is a job (Phase 10); a `send` opts this item in
        extra["jira"] = msg.jira
        if msg.jira.get("send"):
            item.external_refs = {**(item.external_refs or {}), "jira_opt_in": "true"}
            result.jira = {"status": "deferred"}
    primary = ctx.events[0]
    primary.client_id = msg.client_id
    primary.payload = {**primary.payload, **extra, "result": result.as_dict()}
    return result


async def _inline_jira_push(session_factory: async_sessionmaker[AsyncSession], result: MessageResult) -> None:
    """A reporter who asked for Jira gets the key back when Jira answers within a short bound; otherwise the queued job finishes it."""

    st = get_settings()
    if not (st.features.jira_sync and result.jira and result.item and st.jira_inline_timeout_s > 0):
        return
    res = await jira_sync.push_bounded(session_factory, result.item["id"], st.jira_inline_timeout_s)
    if res is None:
        return
    if res.status in ("pushed", "unchanged") and res.key:
        result.jira = {"status": "pushed", "key": res.key}
    elif res.status == "failed":
        result.jira = {"status": "failed", "error": res.reason}


async def ingest_batch(
    session_factory: async_sessionmaker[AsyncSession],
    ws_id: str,
    batch: ClientBatch,
    batch_id: str | None = None,
    agent_run_id: str | None = None,
) -> list[MessageResult]:
    """One transaction per message; a failing message never fails the batch."""
    batch_id = batch_id or new_id()
    results: list[MessageResult] = []
    for msg in batch.messages:
        for attempt in (1, 2):
            try:
                uow = UnitOfWork(session_factory)
                async with uow:
                    if agent_run_id:  # written on behalf of the LLM gateway: every event carries the run id
                        uow.context["agent_run_id"] = agent_run_id
                    ws = await uow.session.get(m.Workspace, ws_id)
                    assert ws is not None  # noqa: S101
                    result = await _process(uow, ws, msg, batch.client_version, batch_id, batch.mode)
                    if result.status == "failed":
                        results.append(result)
                        break
                    await uow.commit()
                    results.append(result)
                await _inline_jira_push(session_factory, result)
                break
            except IntegrityError:
                if (
                    attempt == 1
                ):  # a concurrent delivery of the same client_id won the race: the retry sees it and deduplicates
                    continue
                log.warning("capture_message_failed", client_id=msg.client_id, error="integrity")
                results.append(MessageResult(msg.client_id, "failed", error_code="internal_error"))
            except Exception as exc:
                log.warning("capture_message_failed", client_id=msg.client_id, error=repr(exc))
                results.append(MessageResult(msg.client_id, "failed", error_code="internal_error"))
                break
    return results


# The pipeline used to live in this one module; these names stay importable from here.
__all__ = [
    "CONFIDENCE_FLOOR",
    "DEFAULT_DEV_KEY",
    "DEV_TAG",
    "MATCH_GAP",
    "MATCH_THRESHOLD",
    "RECENCY_BONUS",
    "RECENCY_DAYS",
    "Ctx",
    "MessageResult",
    "add_review",
    "apply_action",
    "apply_fields",
    "apply_hints",
    "dev_project_key",
    "ensure_dev_project",
    "ingest_batch",
    "is_dev_project",
    "resolve_item",
    "resolve_project",
]
