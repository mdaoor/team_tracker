"""Phase 14 review M6 / ADR-0050 amendment: `item.merged` reaches Jira (comment on the source issue + link to the target's)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.integrations.jira import sync
from tt_server.jobs import jira_tasks

from tests.api.conftest import Api
from tests.api.fake_jira import FakeJira
from tests.api.test_acl_jira import connect, jira, new_item  # noqa: F401  # `jira` is a fixture
from tests.api.test_commands import one

ROUTERS_COVERED = ("jira",)


async def _merge(api: Api, a: str, b: str) -> list[str]:
    """Merge b into a through the command; returns the ids of the `item.merged` events of the retired sources."""
    res = await one(api, "item.merge", {"sources": [a, b], "target": a}, confirmed=True)
    assert res["status"] == "applied", res
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    async with sf() as s:
        evs = (await s.scalars(select(m.Event).where(m.Event.event_type == "item.merged"))).all()
    return [e.id for e in evs if (e.payload.get("after") or {}).get("merged_into")]


async def _pushed(api: Api, title: str) -> tuple[dict[str, Any], str]:
    res = await new_item(api, title)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    key = (await sync.push_item(sf, res["item"]["id"], force=True)).key
    assert key
    return res["item"], key


async def test_merging_comments_on_the_source_issue_and_links_it_to_the_target(api: Api, jira: FakeJira) -> None:  # fmt: skip
    await connect(api, auto_send=True)
    a, issue_a = await _pushed(api, "dup a")
    b, issue_b = await _pushed(api, "dup b")
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    ev_ids = await _merge(api, a["key"], b["key"])
    assert len(ev_ids) == 1
    assert await jira_tasks.handle_event(sf, ev_ids[0]) == "pushed"
    assert [c["issue"] for c in jira.comments] == [issue_b]
    text = str(jira.comments[0]["body"])
    assert a["key"] in text and issue_a in text and "merged" in text
    assert jira.links == [{"type": "Duplicate", "inward": issue_a, "outward": issue_b}]
    # a replay of the event (the job retried) announces nothing twice
    assert await jira_tasks.handle_event(sf, ev_ids[0]) == "unchanged"
    assert len(jira.comments) == 1 and len(jira.links) == 1


async def test_merging_when_the_project_projection_is_off_touches_nothing(api: Api, jira: FakeJira) -> None:  # fmt: skip
    await connect(api, auto_send=True)
    a, _ = await _pushed(api, "dup a")
    b, _ = await _pushed(api, "dup b")
    off = await api.call("PUT", "/projects/WEB/jira", json={"enabled": False, "project_key": "WEB", "issuetype_map": {"task": "Task"}})  # fmt: skip
    assert off.status_code == 200, off.text
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    (ev,) = await _merge(api, a["key"], b["key"])
    assert await jira_tasks.handle_event(sf, ev) == "skipped"
    assert jira.comments == [] and jira.links == []


async def test_a_source_without_an_issue_is_skipped_and_a_target_without_one_gets_the_comment_only(api: Api, jira: FakeJira, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    await connect(api)  # not auto_send: only items pushed by hand have issues
    plain = (await new_item(api, "never sent"))["item"]
    b, issue_b = await _pushed(api, "sent")
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    # source b (has an issue) merged into `plain` (has none, and is not opted in): comment, no link
    (ev,) = await _merge(api, plain["key"], b["key"])
    assert await jira_tasks.handle_event(sf, ev) == "pushed"
    assert [c["issue"] for c in jira.comments] == [issue_b] and jira.links == []
    async with session_factory() as s:
        log = (await s.scalars(select(m.JiraSyncLog).where(m.JiraSyncLog.action == "merge"))).all()
    assert len(log) == 1 and log[0].ok and log[0].diff["linked"] is False


async def test_a_missing_link_type_still_posts_the_comment(api: Api, jira: FakeJira, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    await connect(api, auto_send=True)
    a, _ = await _pushed(api, "dup a")
    b, issue_b = await _pushed(api, "dup b")
    jira.link_types.discard("Duplicate")
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    (ev,) = await _merge(api, a["key"], b["key"])
    assert await jira_tasks.handle_event(sf, ev) == "pushed"
    assert [c["issue"] for c in jira.comments] == [issue_b] and jira.links == []
    async with session_factory() as s:
        log = (await s.scalars(select(m.JiraSyncLog).where(m.JiraSyncLog.action == "merge"))).all()
    assert log[0].diff["linked"] is False and "link_error" in log[0].diff
