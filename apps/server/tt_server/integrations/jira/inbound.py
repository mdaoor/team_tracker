"""Inbound webhook: only `status`, `assignee` and comments come back, as `source=import` events through the normal services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, TargetType
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.domain.items import service as items
from tt_server.domain.projects.service import workflow_states
from tt_server.integrations.jira import mapping
from tt_server.integrations.jira.client import adf_text
from tt_server.integrations.jira.sync import _log, finish, project_config


async def _bot(session: AsyncSession, ws_id: str) -> str | None:
    return await session.scalar(
        select(m.JiraConnection.bot_account_id).where(m.JiraConnection.workspace_id == ws_id)
    )


async def handle(
    factory: async_sessionmaker[AsyncSession], ws_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply one webhook delivery. Returns `{"handled": ..., "ignored": reason?}` (also what the endpoint answers)."""
    kind = str(payload.get("webhookEvent", ""))
    if kind not in ("jira:issue_updated", "comment_created"):
        return {"ignored": "event_type"}
    issue = payload.get("issue") or {}
    key = issue.get("key")
    async with UnitOfWork(factory) as uow:
        s = uow.session
        link = await s.scalar(
            select(m.JiraLink).join(m.WorkItem, m.WorkItem.id == m.JiraLink.item_id)
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .where(m.JiraLink.issue_key == key, m.Project.workspace_id == ws_id)
        )  # fmt: skip
        if link is None:
            return {"ignored": "unknown_issue"}
        actor = (payload.get("user") or payload.get("comment", {}).get("author") or {}).get("accountId")
        if actor and actor == await _bot(s, ws_id):
            return {"ignored": "own_change"}  # loop prevention: we made this change
        item = await s.get(m.WorkItem, link.item_id)
        assert item is not None  # noqa: S101
        project = await s.get(m.Project, item.project_id)
        ws = await s.get(m.Workspace, ws_id)
        assert project is not None and ws is not None  # noqa: S101
        cfg = project_config(project)
        sysp = await system_person(s)
        uow.context["actor_id"] = sysp.id
        out: dict[str, Any] = {"handled": []}
        if kind == "comment_created":
            if not cfg.get("sync_comments"):
                return {"ignored": "comments_off"}
            c = payload.get("comment") or {}
            if await s.scalar(
                select(m.Event.id).where(
                    m.Event.workspace_id == ws_id,
                    m.Event.payload["after"]["jira_comment_id"].astext == str(c.get("id")),
                )
            ):
                return {"ignored": "duplicate"}
            author = await _person_by_email(s, (c.get("author") or {}).get("emailAddress"))
            name = (c.get("author") or {}).get("displayName", "Jira user")
            body = adf_text(c.get("body"))
            row = m.Comment(
                id=new_id(), target_type=TargetType.WORK_ITEM, target_id=item.id, author_id=(author.id if author else sysp.id),
                body=body if author else f"[Jira · {name}] {body}",
            )  # fmt: skip
            s.add(row)
            emit(
                uow, workspace_id=ws_id, actor_id=row.author_id, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
                event_type="comment.added", payload={"after": {"comment_id": row.id, "jira_comment_id": str(c.get("id")), "issue_key": key}},
                source=EventSource.IMPORT,
            )  # fmt: skip
            out["handled"].append("comment")
        for ch in (payload.get("changelog") or {}).get("items", []):
            if ch.get("field") == "status":
                out["handled"].append(
                    await _status(uow, ws, project, item, link, cfg, str(ch.get("toString", "")), sysp.id)
                )
            elif ch.get("field") == "assignee":
                out["handled"].append(await _assignee(uow, ws, item, issue, link, sysp.id))
        link.last_inbound_at = utcnow()
        await _log(s, ws_id, item.id, key, kind, direction="inbound", diff={"handled": out["handled"]})
        await finish(uow)
        return out


async def _person_by_email(s: AsyncSession, email: str | None) -> m.Person | None:
    if not email:
        return None
    return await s.scalar(select(m.Person).where(m.Person.email == email.lower()))


async def _status(
    uow: UnitOfWork,
    ws: m.Workspace,
    project: m.Project,
    item: m.WorkItem,
    link: m.JiraLink,
    cfg: dict[str, Any],
    jira_status: str,
    actor: str,
) -> str:
    s = uow.session
    state_key = mapping.reverse_status(cfg, jira_status)
    if state_key is None:
        if not await s.scalar(
            select(m.ReviewItem.id).where(
                m.ReviewItem.workspace_id == ws.id, m.ReviewItem.kind == "jira_mapping_gap", m.ReviewItem.status == "open",
                m.ReviewItem.suggestion["field"].astext == "status_map", m.ReviewItem.suggestion["jira_status"].astext == jira_status,
                m.ReviewItem.suggestion["project"].astext == project.key,
            )
        ):  # fmt: skip
            s.add(
                m.ReviewItem(
                    id=new_id(), workspace_id=ws.id, kind="jira_mapping_gap", target_type="work_item", target_id=item.id,
                    suggestion={"project": project.key, "field": "status_map", "jira_status": jira_status, "key": None},
                )
            )  # fmt: skip
            emit(
                uow, workspace_id=ws.id, actor_id=actor, entity_type=TargetType.WORK_ITEM, entity_id=item.id, event_type="review.created",
                payload={"after": {"kind": "jira_mapping_gap", "jira_status": jira_status}}, source=EventSource.RULE,
            )  # fmt: skip
        return f"status_unmapped:{jira_status}"
    state = next((st for st in await workflow_states(s, project.workflow_id) if st.key == state_key), None)
    if state is None:
        return f"status_unknown_state:{state_key}"
    if state.id == item.state_id:
        link.last_state_key = state_key
        return "status_already"  # the echo of our own transition, or a no-op change
    old, new = await items.set_state(uow, item, state)
    link.last_state_key = state_key  # Jira already has it: do not push it back
    emit(
        uow, workspace_id=ws.id, actor_id=actor, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
        event_type="item.reopened" if old.value == "done" and new.value != "done" else "item.state_changed",
        payload={"before": {"category": old.value}, "after": {"category": new.value, "state": state.key}, "cause": "jira", "issue_key": link.issue_key},
        source=EventSource.IMPORT,
    )  # fmt: skip
    return f"status:{state_key}"


async def _assignee(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, issue: dict[str, Any], link: m.JiraLink, actor: str
) -> str:
    s = uow.session
    a = (issue.get("fields") or {}).get("assignee")
    if a is None:
        new_id_ = None
    else:
        person = await _person_by_email(s, a.get("emailAddress"))
        if person is None:
            return "assignee_unmapped"
        new_id_ = person.id
    if new_id_ == item.assignee_id:
        return "assignee_already"
    before = item.assignee_id
    item.assignee_id = new_id_
    emit(
        uow, workspace_id=ws.id, actor_id=actor, entity_type=TargetType.WORK_ITEM, entity_id=item.id, event_type="item.updated",
        payload={"before": {"assignee_id": before}, "after": {"assignee_id": new_id_}, "cause": "jira", "issue_key": link.issue_key},
        source=EventSource.IMPORT,
    )  # fmt: skip
    return "assignee"
