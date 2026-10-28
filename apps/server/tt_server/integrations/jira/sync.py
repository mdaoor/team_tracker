"""Outbound push: one item at a time, serialised by an advisory lock, idempotent under retry (Phase 10)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, TargetType
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, system_person
from tt_server.integrations.jira import crypto, mapping
from tt_server.integrations.jira.client import JiraClient, JiraError, JiraUnavailable, adf

# Tests inject a transport (and a no-op sleep) here; production leaves both unset.
CLIENT_KW: dict[str, Any] = {}
MAX_ATTEMPTS = 5
_ACCOUNTS: dict[tuple[str, str], str | None] = {}


@dataclass
class PushResult:
    status: str  # pushed | unchanged | skipped | failed | pending
    key: str | None = None
    reason: str | None = None
    changed: list[str] | None = None


def make_client(conn: m.JiraConnection) -> JiraClient:
    return JiraClient(conn.base_url, conn.email, crypto.decrypt(conn.token_enc), **CLIENT_KW)


async def connection(session: AsyncSession, ws_id: str) -> m.JiraConnection | None:
    return await session.scalar(
        select(m.JiraConnection).where(
            m.JiraConnection.workspace_id == ws_id,
            m.JiraConnection.enabled.is_(True),
            m.JiraConnection.deleted_at.is_(None),
        )
    )


def project_config(project: m.Project) -> dict[str, Any]:
    return dict((project.settings or {}).get("jira") or {})


def opted_in(item: m.WorkItem, cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("auto_send")) or (item.external_refs or {}).get("jira_opt_in") == "true"


async def _account(client: JiraClient, conn_id: str, email: str) -> str | None:
    k = (conn_id, email.lower())
    if k not in _ACCOUNTS:
        _ACCOUNTS[k] = await client.find_user(email)
    return _ACCOUNTS[k]


async def _item_view(
    session: AsyncSession, item: m.WorkItem
) -> tuple[dict[str, Any], str | None, str | None]:
    itype = await session.get(m.WorkItemType, item.type_id)
    state = await session.get(m.WorkflowState, item.state_id)
    tags = (
        await session.scalars(
            select(m.Tag.name)
            .join(m.TagLink, m.TagLink.tag_id == m.Tag.id)
            .where(
                m.TagLink.target_type == TargetType.WORK_ITEM,
                m.TagLink.target_id == item.id,
                m.TagLink.deleted_at.is_(None),
            )
        )
    ).all()
    stage = await session.scalar(
        select(m.Stage.key)
        .join(m.StagePlacement, m.StagePlacement.stage_id == m.Stage.id)
        .where(
            m.StagePlacement.target_type == TargetType.WORK_ITEM,
            m.StagePlacement.target_id == item.id,
            m.StagePlacement.exited_at.is_(None),
        )
    )
    hours = None
    if item.estimate_value is not None:
        unit = item.estimate_unit.value
        hours = (
            float(item.estimate_value) * (8 if unit == "days" else 1) if unit in ("hours", "days") else None
        )
    email = None
    if item.assignee_id:
        p = await session.get(m.Person, item.assignee_id)
        email = p.email if p else None
    view = {
        "id": item.id, "title": item.title, "description": item.description, "type": itype.key.value if itype else "task",
        "priority": item.priority.value, "planned_end": item.planned_end, "estimate_hours": hours, "tags": list(tags),
        "state_key": state.key if state else "open", "assignee_unassigned": item.assignee_id is None,
    }  # fmt: skip
    return view, email, stage


async def _log(
    session: AsyncSession, ws_id: str, item_id: str | None, key: str | None, action: str, *, ok: bool = True,
    diff: dict[str, Any] | None = None, error: str | None = None, direction: str = "push",
) -> None:  # fmt: skip
    session.add(
        m.JiraSyncLog(
            id=new_id(), workspace_id=ws_id, item_id=item_id, issue_key=key, direction=direction, action=action,
            ok=ok, diff=diff or {}, error=error,
        )
    )  # fmt: skip


async def _gap_rows(
    uow: UnitOfWork, ws: m.Workspace, project: m.Project, item: m.WorkItem, gaps: list[mapping.Gap]
) -> None:
    s = uow.session
    for g in gaps:
        sug = {"project": project.key, "field": g.field, "key": g.key}
        dup = await s.scalar(
            select(m.ReviewItem.id).where(
                m.ReviewItem.workspace_id == ws.id,
                m.ReviewItem.kind == "jira_mapping_gap",
                m.ReviewItem.status == "open",
                m.ReviewItem.suggestion["project"].astext == project.key,
                m.ReviewItem.suggestion["field"].astext == g.field,
                m.ReviewItem.suggestion["key"].astext == g.key,
            )
        )
        if dup:
            continue
        row = m.ReviewItem(
            id=new_id(),
            workspace_id=ws.id,
            kind="jira_mapping_gap",
            target_type="work_item",
            target_id=item.id,
            suggestion=sug,
        )
        s.add(row)
        sysp = await system_person(s)
        emit(
            uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
            event_type="review.created", payload={"after": {"review_item_id": row.id, "kind": "jira_mapping_gap", **sug}},
            source=EventSource.RULE,
        )  # fmt: skip


async def _failure_row(uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, message: str) -> None:
    s = uow.session
    if await s.scalar(
        select(m.ReviewItem.id).where(
            m.ReviewItem.workspace_id == ws.id, m.ReviewItem.kind == "jira_sync_failed",
            m.ReviewItem.status == "open", m.ReviewItem.target_id == item.id,
        )
    ):  # fmt: skip
        return
    s.add(
        m.ReviewItem(
            id=new_id(), workspace_id=ws.id, kind="jira_sync_failed", target_type="work_item", target_id=item.id,
            suggestion={"error": message[:300]},
        )
    )  # fmt: skip
    sysp = await system_person(s)
    emit(
        uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
        event_type="review.created", payload={"after": {"kind": "jira_sync_failed", "error": message[:300]}}, source=EventSource.RULE,
    )  # fmt: skip


async def _ensure_status(client: JiraClient, key: str, target: str) -> str:
    """Move the issue to `target` (a status name). Returns done | already | no_path."""
    cur = (await client.get_issue(key)).get("fields", {}).get("status", {}).get("name", "")
    if cur.lower() == target.lower():
        return "already"
    for t in await client.transitions(key):
        if (
            str(t.get("to", {}).get("name", "")).lower() == target.lower()
            or str(t.get("name", "")).lower() == target.lower()
        ):
            await client.transition(key, str(t["id"]))
            return "done"
    return "no_path"


async def finish(uow: UnitOfWork) -> None:
    """Commit; sync bookkeeping (links, logs) alone is not a domain fact and needs no Event."""
    if len(uow.events):
        await uow.commit()
    else:
        await uow.commit_bookkeeping()


async def push_item(
    factory: async_sessionmaker[AsyncSession],
    item_id: str,
    *,
    force: bool = False,
    client: JiraClient | None = None,
) -> PushResult:
    """Bring the Jira issue for `item_id` in line with the item. Safe to call repeatedly and concurrently."""
    async with UnitOfWork(factory) as uow:
        s = uow.session
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"jira:{item_id}"})
        item = await s.get(m.WorkItem, item_id)
        if item is None or item.deleted_at is not None:
            return PushResult("skipped", reason="no_item")
        project = await s.get(m.Project, item.project_id)
        assert project is not None  # noqa: S101
        ws = await s.get(m.Workspace, project.workspace_id)
        assert ws is not None  # noqa: S101
        ws_id, item_pk = ws.id, item.id
        cfg = project_config(project)
        conn = await connection(s, ws.id)
        if conn is None:
            return PushResult("skipped", reason="no_connection")
        if not cfg.get("enabled") or not cfg.get("project_key"):
            return PushResult("skipped", reason="project_not_linked")
        if not (force or opted_in(item, cfg)):
            return PushResult("skipped", reason="not_opted_in")
        own = client is None
        client = client or make_client(conn)
        link = await s.scalar(select(m.JiraLink).where(m.JiraLink.item_id == item.id))
        try:
            view, email, stage = await _item_view(s, item)
            account = await _account(client, conn.id, email) if email else None
            want = mapping.desired(view, cfg, assignee_account=account, stage_key=stage)
            blocking = [g for g in want.gaps if g.field == "issuetype_map"] if link is None else []
            await _gap_rows(uow, ws, project, item, want.gaps)
            if blocking:
                await uow.commit()
                return PushResult("pending", reason="mapping_gap")
            changed: list[str] = []
            if link is None:
                found = await client.search(f'project = "{cfg["project_key"]}" AND labels = "tt:{item.id}"')
                if found:
                    issue = found[0]
                    await client.update_issue(issue["key"], want.fields)
                else:
                    issue = await client.create_issue({**want.create_only, **want.fields})
                link = m.JiraLink(
                    id=new_id(), item_id=item.id, issue_key=issue["key"], issue_id=str(issue.get("id", "")), status="synced",
                )  # fmt: skip
                s.add(link)
                item.external_refs = {**(item.external_refs or {}), "jira": link.issue_key}
                sysp = await system_person(s)
                emit(
                    uow, workspace_id=ws.id, actor_id=sysp.id, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
                    event_type="item.jira_linked", payload={"after": {"issue_key": link.issue_key}}, source=EventSource.IMPORT,
                )  # fmt: skip
                changed = ["created"]
                await _log(s, ws.id, item.id, link.issue_key, "create", diff={"fields": sorted(want.fields)})
            elif force or mapping.diff(link.last_pushed_hash, want):
                await client.update_issue(link.issue_key, want.fields)
                changed = ["fields"]
                await _log(s, ws.id, item.id, link.issue_key, "update", diff={"fields": sorted(want.fields)})
            link.last_pushed_hash = want.hash
            if want.target_status and (force or link.last_state_key != want.state_key):
                outcome = await _ensure_status(client, link.issue_key, want.target_status)
                if outcome == "no_path":
                    await client.add_comment(
                        link.issue_key,
                        adf(
                            f"Team Tracker state is now '{want.state_key}'; no Jira transition to '{want.target_status}' exists from here."
                        ),
                    )
                changed.append(f"status:{outcome}")
                await _log(
                    s,
                    ws.id,
                    item.id,
                    link.issue_key,
                    "transition",
                    diff={"to": want.target_status, "outcome": outcome},
                )
            link.last_state_key = want.state_key
            link.last_pushed_at = utcnow()
            link.status, link.attempts, link.last_error = "synced", 0, None
            await finish(uow)  # the item.jira_linked event (if any) commits with the link, not after it
            if cfg.get("sync_time_logs"):
                n = await _push_worklogs(uow, client, ws, item, link)
                if n:
                    changed.append(f"worklogs:{n}")
            await finish(uow)
            return PushResult("pushed" if changed else "unchanged", key=link.issue_key, changed=changed)
        except JiraUnavailable as exc:
            await s.rollback()
            await _record_failure(uow, ws_id, item_pk, str(exc), transient=True)
            raise
        except JiraError as exc:
            await s.rollback()
            await _record_failure(uow, ws_id, item_pk, str(exc), transient=False)
            return PushResult("failed", reason=str(exc))
        finally:
            if own:
                await client.aclose()


async def _push_worklogs(
    uow: UnitOfWork, client: JiraClient, ws: m.Workspace, item: m.WorkItem, link: m.JiraLink
) -> int:
    s = uow.session
    done = set(
        (await s.scalars(select(m.JiraWorklog.time_entry_id).where(m.JiraWorklog.item_id == item.id))).all()
    )
    entries = (
        await s.scalars(
            select(m.TimeEntry)
            .where(m.TimeEntry.work_item_id == item.id, m.TimeEntry.deleted_at.is_(None))
            .order_by(m.TimeEntry.date, m.TimeEntry.id)
        )
    ).all()
    n = 0
    for te in entries:
        if te.id in done:
            continue
        started = datetime.combine(te.date, time(9, 0), tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        wl = await client.add_worklog(link.issue_key, te.minutes * 60, started, te.note)
        s.add(
            m.JiraWorklog(id=new_id(), time_entry_id=te.id, item_id=item.id, worklog_id=str(wl.get("id", "")))
        )
        await _log(
            s,
            ws.id,
            item.id,
            link.issue_key,
            "worklog",
            diff={"minutes": te.minutes, "date": te.date.isoformat()},
        )
        await (
            uow.commit_bookkeeping()
        )  # one row per worklog, committed as soon as Jira has it: a retry never double-posts
        n += 1
    return n


async def _record_failure(uow: UnitOfWork, ws_id: str, item_id: str, error: str, *, transient: bool) -> None:
    s = uow.session
    item = await s.get(m.WorkItem, item_id)
    link = await s.scalar(select(m.JiraLink).where(m.JiraLink.item_id == item_id))
    if link is not None:
        link.status, link.attempts, link.last_error = "failed", link.attempts + 1, error[:300]
    await _log(s, ws_id, item_id, link.issue_key if link else None, "push", ok=False, error=error[:300])
    attempts = link.attempts if link else 1
    await uow.commit_bookkeeping()
    if item is not None and (not transient or attempts >= MAX_ATTEMPTS):
        ws = await s.get(m.Workspace, ws_id)
        assert ws is not None  # noqa: S101
        await _failure_row(uow, ws, item, error)
        await uow.commit()


async def push_bounded(
    factory: async_sessionmaker[AsyncSession], item_id: str, timeout_s: float
) -> PushResult | None:
    """Capture-path helper: try the push for an opted-in item for at most `timeout_s`; None = left to the queue."""
    if timeout_s <= 0:
        return None
    try:
        return await asyncio.wait_for(push_item(factory, item_id), timeout_s)
    except (TimeoutError, JiraUnavailable, JiraError, httpx.HTTPError):
        return None


async def push_merge(
    factory: async_sessionmaker[AsyncSession], source_id: str, target_key: str, *, client: JiraClient | None = None
) -> PushResult:
    """Project `item.merged` (ADR-0050 amendment): when the source item has a Jira issue, link it to the target's issue
    (a "Duplicate" link, configurable as `jira.merge_link_type`) and leave a comment saying where the work went. A target with
    no issue yet is pushed first when its own project opts it in; if it still has none, only the comment is posted. Idempotent:
    a source already announced (a successful `merge` log row) is left alone."""
    async with UnitOfWork(factory) as uow:
        s = uow.session
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"jira:{source_id}"})
        src = await s.get(m.WorkItem, source_id)  # soft-deleted by the merge: read it anyway
        if src is None:
            return PushResult("skipped", reason="no_item")
        project = await s.get(m.Project, src.project_id)
        assert project is not None  # noqa: S101
        cfg = project_config(project)
        conn = await connection(s, project.workspace_id)
        if conn is None:
            return PushResult("skipped", reason="no_connection")
        if not cfg.get("enabled") or not cfg.get("project_key"):
            return PushResult("skipped", reason="project_not_linked")
        link = await s.scalar(select(m.JiraLink).where(m.JiraLink.item_id == src.id))
        if link is None:
            return PushResult("skipped", reason="no_link")
        if await s.scalar(
            select(m.JiraSyncLog.id).where(
                m.JiraSyncLog.item_id == src.id, m.JiraSyncLog.action == "merge", m.JiraSyncLog.ok.is_(True)
            )
        ):
            return PushResult("unchanged", key=link.issue_key, reason="already_announced")
        ws_id, src_key, src_issue = project.workspace_id, src.key, link.issue_key
        tgt = await s.scalar(
            select(m.WorkItem).where(
                m.WorkItem.key == target_key,
                m.WorkItem.project_id.in_(select(m.Project.id).where(m.Project.workspace_id == ws_id)),
            )
        )
        tgt_id = tgt.id if tgt else None
    target_issue: str | None = None
    if tgt_id is not None:
        await push_item(factory, tgt_id, client=client)  # a no-op when it is linked and unchanged or not opted in
        async with factory() as rs:
            target_issue = await rs.scalar(select(m.JiraLink.issue_key).where(m.JiraLink.item_id == tgt_id))
    own = client is None
    async with UnitOfWork(factory) as uow:
        s = uow.session
        conn = await connection(s, ws_id)
        if conn is None:
            return PushResult("skipped", reason="no_connection")
        client = client or make_client(conn)
        linked, link_error = False, None
        try:
            if target_issue:
                try:
                    await client.link_issues(
                        target_issue, src_issue, str(cfg.get("merge_link_type") or "Duplicate")
                    )
                    linked = True
                except JiraError as exc:  # e.g. the link type does not exist in this Jira: the comment still helps
                    link_error = str(exc)
            where = f"{target_key} ({target_issue})" if target_issue else target_key
            await client.add_comment(
                src_issue,
                adf(
                    f"Team Tracker: {src_key} was merged into {where}. Its time and evidence now live on {target_key}."
                ),
            )
        except JiraUnavailable:
            raise  # the task retries; nothing was logged, so the retry announces it
        except JiraError as exc:
            await _log(s, ws_id, source_id, src_issue, "merge", ok=False, error=str(exc)[:300], diff={"into": target_key})  # fmt: skip
            await uow.commit_bookkeeping()
            return PushResult("failed", key=src_issue, reason=str(exc))
        finally:
            if own:
                await client.aclose()
        diff: dict[str, Any] = {"into": target_key, "target_issue": target_issue, "linked": linked}
        if link_error:
            diff["link_error"] = link_error[:200]
        await _log(s, ws_id, source_id, src_issue, "merge", ok=True, diff=diff)
        await uow.commit_bookkeeping()
        return PushResult("pushed", key=src_issue, changed=["comment", *(["link"] if linked else [])])
