"""Phase 10: Jira projection (outbound push, inbound webhook, loop prevention, retries, mapping gaps) against an in-memory Jira."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.enums import EventSource
from tt_server.db import models as m
from tt_server.integrations.jira import sync
from tt_server.integrations.jira.client import JiraUnavailable
from tt_server.settings import get_settings

from tests.api.conftest import Api
from tests.api.fake_jira import FakeJira

ROUTERS_COVERED = ("jira",)

CFG: dict[str, Any] = {
    "enabled": True, "project_key": "WEB", "issuetype_map": {"task": "Task", "bug": "Bug"},
    "priority_map": {"medium": "Medium", "high": "High"},
    "status_map": {"todo": "To Do", "doing": "In Progress", "done": "Done"},
    "auto_send": False, "sync_time_logs": True, "sync_comments": True,
}  # fmt: skip
sleeps: list[float] = []


async def _nosleep(s: float) -> None:
    sleeps.append(s)


@pytest.fixture
def jira() -> AsyncIterator[FakeJira]:
    fj = FakeJira()
    sleeps.clear()
    old = dict(sync.CLIENT_KW)
    sync.CLIENT_KW.update(transport=fj.transport(), sleep=_nosleep, backoff_s=0.0)
    sync._ACCOUNTS.clear()
    feats = get_settings().features
    was = feats.jira_sync
    feats.jira_sync = True
    yield fj  # type: ignore[misc]
    feats.jira_sync = was
    sync.CLIENT_KW.clear()
    sync.CLIENT_KW.update(old)


async def connect(api: Api, **cfg: Any) -> str:
    r = await api.call(
        "PUT",
        "/integrations/jira",
        json={"base_url": "https://acme.atlassian.net", "email": "bot@acme.test", "api_token": "tok-123456"},
    )
    assert r.status_code == 200, r.text
    secret = r.json()["webhook_secret"]
    assert (await api.call("POST", "/integrations/jira:test")).json()["ok"] is True
    mapped = await api.call("PUT", "/projects/WEB/jira", json={**CFG, **cfg})
    assert mapped.status_code == 200, mapped.text
    return secret  # type: ignore[no-any-return]


async def new_item(api: Api, title: str = "fix login", send: bool = False, **extra: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"action": "create", "item": {"title": title}, "project": "WEB", **extra}
    if send:
        msg["jira"] = {"project": "WEB", "issuetype": "Task", "send": True}
    return await api.send(**msg)


async def hook(api: Api, ws_secret: str, payload: dict[str, Any], **kw: Any) -> Any:
    return await api.c.post(
        "/api/v1/integrations/jira/webhook/acme", params={"token": ws_secret}, json=payload, **kw
    )


def status_change(key: str, to: str, actor: str = "acc-human") -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_updated", "user": {"accountId": actor}, "issue": {"key": key, "fields": {}},
        "changelog": {"items": [{"field": "status", "toString": to}]},
    }  # fmt: skip


async def state_of(api: Api, key: str) -> str:
    return (await api.call("GET", f"/items/{key}")).json()["state"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------- connection and secrets
async def test_the_token_is_stored_encrypted_and_never_returned(
    api: Api, session_factory: async_sessionmaker[AsyncSession], jira: FakeJira
) -> None:
    await connect(api)
    async with session_factory() as s:
        row = await s.scalar(select(m.JiraConnection))
    assert row is not None and "tok-123456" not in row.token_enc and row.bot_account_id == "bot-acc"
    got = (await api.call("GET", "/integrations/jira")).json()
    assert got["configured"] and "api_token" not in got and "webhook_secret" not in got
    assert (await api.call("GET", "/integrations/jira", as_="@sara")).status_code == 403
    again = (await api.call("POST", "/integrations/jira:rotate-secret")).json()["webhook_secret"]
    assert again and len(again) > 20


async def test_mapping_is_validated_and_needs_project_write(api: Api, jira: FakeJira) -> None:
    await connect(api)
    bad = await api.call("PUT", "/projects/WEB/jira", json={"enabled": True})
    assert bad.status_code == 400 and "project_key" in bad.json()["error"]["message"]
    assert (await api.call("PUT", "/projects/WEB/jira", json={"status_map": {"a": 1}})).status_code == 400
    assert (await api.call("PUT", "/projects/WEB/jira", as_="@ali", json=CFG)).status_code in (403, 404)
    opts = (await api.call("POST", "/integrations/jira:test", params={"project": "WEB"})).json()["options"]
    assert "In Progress" in opts["statuses"] and opts["fields"][0]["id"] == "customfield_10001"


# ---------------------------------------------------------------- outbound
async def test_a_capture_with_jira_send_creates_the_issue_and_returns_its_key(
    api: Api, jira: FakeJira
) -> None:
    await connect(api)
    res = await new_item(api, send=True)
    assert res["jira"]["status"] == "pushed" and res["jira"]["key"].startswith("WEB-")
    issue = jira.issues[res["jira"]["key"]]
    assert (
        issue["fields"]["summary"] == "fix login" and f"tt:{res['item']['id']}" in issue["fields"]["labels"]
    )
    assert issue["fields"]["issuetype"] == {"name": "Task"} and issue["fields"]["project"] == {"key": "WEB"}
    item = (await api.call("GET", f"/items/{res['item']['key']}")).json()
    assert item["external_refs"]["jira"] == res["jira"]["key"]
    info = (await api.call("GET", f"/items/{res['item']['key']}/jira")).json()
    assert info["linked"] and info["status"] == "synced"


async def test_nothing_is_pushed_without_auto_send_or_an_opt_in(api: Api, jira: FakeJira) -> None:
    await connect(api)
    res = await new_item(api)  # no jira block
    assert res.get("jira") is None
    iid = res["item"]["id"]

    out = await sync.push_item(api.c._transport.app.state.session_factory, iid)  # type: ignore[attr-defined]
    assert out.status == "skipped" and out.reason == "not_opted_in" and jira.issues == {}
    # a project with auto_send pushes everything
    await api.call("PUT", "/projects/WEB/jira", json={**CFG, "auto_send": True})
    out = await sync.push_item(api.c._transport.app.state.session_factory, iid)
    assert out.status == "pushed" and len(jira.issues) == 1


async def test_done_in_tt_transitions_the_issue_and_unchanged_pushes_send_nothing(
    api: Api, jira: FakeJira
) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    first = await sync.push_item(sf, res["item"]["id"])
    key = first.key
    assert key and jira.issues[key]["status"] == "To Do"
    await api.call("POST", f"/items/{res['item']['key']}/state", json={"state": "doing"})
    await sync.push_item(sf, res["item"]["id"])
    assert jira.issues[key]["status"] == "In Progress"
    await api.call("POST", f"/items/{res['item']['key']}/state", json={"state": "done"})
    out = await sync.push_item(sf, res["item"]["id"])
    assert jira.issues[key]["status"] == "Done" and any(c.startswith("status:") for c in out.changed or [])
    n = len(jira.calls)
    again = await sync.push_item(sf, res["item"]["id"])
    assert again.status == "unchanged"
    assert [c for c in jira.calls[n:] if c[0] in ("PUT", "POST")] == []  # only reads, no writes


async def test_a_state_with_no_jira_path_leaves_a_comment_instead(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    await sync.push_item(sf, res["item"]["id"])
    jira.blocked_statuses.add("In Progress")
    await api.call("POST", f"/items/{res['item']['key']}/state", json={"state": "doing"})
    out = await sync.push_item(sf, res["item"]["id"])
    assert "status:no_path" in (out.changed or []) and jira.comments and "doing" in str(jira.comments[0])


async def test_time_entries_become_worklogs_exactly_once(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api, minutes=30)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    await sync.push_item(sf, res["item"]["id"])
    await sync.push_item(sf, res["item"]["id"], force=True)
    assert len(jira.worklogs) == 1 and jira.worklogs[0]["timeSpentSeconds"] == 1800
    await api.send(action="log_time", item={"key": res["item"]["key"]}, minutes=45)
    await sync.push_item(sf, res["item"]["id"])
    assert [w["timeSpentSeconds"] for w in jira.worklogs] == [1800, 2700]
    async with sf() as s:
        assert await s.scalar(select(func.count()).select_from(m.JiraWorklog)) == 2


async def test_a_failure_halfway_through_worklogs_never_double_posts(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api, minutes=10)
    await api.send(action="log_time", item={"key": res["item"]["key"]}, minutes=20)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    orig = jira._handle
    seen = {"n": 0}

    def flaky(req: Any) -> Any:
        if req.url.path.endswith("/worklog"):
            seen["n"] += 1
            if seen["n"] == 2:
                jira.down = True
        return orig(req)

    sync.CLIENT_KW["transport"] = __import__("httpx").MockTransport(flaky)
    with pytest.raises(JiraUnavailable):
        await sync.push_item(sf, res["item"]["id"])
    jira.down = False
    await sync.push_item(sf, res["item"]["id"])
    assert sorted(w["timeSpentSeconds"] for w in jira.worklogs) == [600, 1200]


# ---------------------------------------------------------------- resilience
async def test_an_outage_never_slows_capture_and_the_retry_catches_up(api: Api, jira: FakeJira) -> None:
    await connect(api)
    jira.down = True
    res = await new_item(api, send=True)
    assert res["status"] in ("applied", "applied_with_warnings") and res["jira"] == {"status": "deferred"}
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    with pytest.raises(JiraUnavailable):
        await sync.push_item(sf, res["item"]["id"])
    info = (await api.call("GET", f"/items/{res['item']['key']}/jira")).json()
    assert info["linked"] is False
    jira.down = False
    out = await sync.push_item(sf, res["item"]["id"])
    assert out.status == "pushed" and len(jira.issues) == 1


async def test_a_retried_create_finds_the_issue_it_already_made(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    jira.lose_create_response = True
    with pytest.raises(JiraUnavailable):
        await sync.push_item(sf, res["item"]["id"])
    assert len(jira.issues) == 1
    out = await sync.push_item(sf, res["item"]["id"])
    assert out.status == "pushed" and len(jira.issues) == 1  # adopted by its tt:<id> label, not duplicated


async def test_rate_limits_are_waited_out_with_retry_after(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    jira.retry_after = "7"
    jira.fail_with = [429, 429]
    out = await sync.push_item(sf, res["item"]["id"])
    assert out.status == "pushed" and sleeps[:2] == [7.0, 7.0]


async def test_a_jira_rejection_is_recorded_as_a_review_row_and_not_retried(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    jira.fail_with = [400]
    out = await sync.push_item(sf, res["item"]["id"])
    assert out.status == "failed"
    rows = (await api.call("GET", "/review-queue", params={"kind": "jira_sync_failed"})).json()["data"]
    assert len(rows) == 1
    log = (await api.call("GET", "/integrations/jira/log")).json()["data"]
    assert any(not r["ok"] for r in log)


# ---------------------------------------------------------------- mapping gaps
async def test_a_mapping_gap_becomes_a_review_row_with_a_one_click_fix(api: Api, jira: FakeJira) -> None:
    await connect(api, auto_send=True, issuetype_map={})
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    out = await sync.push_item(sf, res["item"]["id"])
    assert out.status == "pending" and out.reason == "mapping_gap" and jira.issues == {}
    rows = (await api.call("GET", "/review-queue", params={"kind": "jira_mapping_gap"})).json()["data"]
    assert [r["suggestion"]["field"] for r in rows] == ["issuetype_map"] and rows[0]["suggestion"][
        "key"
    ] == "task"
    again = await sync.push_item(sf, res["item"]["id"])
    assert (await api.call("GET", "/review-queue", params={"kind": "jira_mapping_gap"})).json()[
        "data"
    ].__len__() == 1  # deduped
    assert again.status == "pending"
    fix = await api.call(
        "POST", "/review-queue/resolve", json={"ref": rows[0]["ref"], "choice": {"map_to": "Task"}}
    )
    assert fix.status_code == 200, fix.text
    assert (await api.call("GET", "/projects/WEB/jira")).json()["config"]["issuetype_map"] == {"task": "Task"}
    assert (await sync.push_item(sf, res["item"]["id"])).status == "pushed"


# ---------------------------------------------------------------- inbound
async def test_a_status_change_in_jira_moves_the_item_as_an_import_event(
    api: Api, jira: FakeJira, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    secret = await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    key = (await sync.push_item(sf, res["item"]["id"])).key
    assert key
    r = await hook(api, secret, status_change(key, "In Progress"))
    assert r.status_code == 200 and "status:doing" in r.json()["handled"]
    assert await state_of(api, res["item"]["key"]) == "doing"
    async with session_factory() as s:
        ev = (
            await s.scalars(
                select(m.Event).where(
                    m.Event.entity_id == res["item"]["id"], m.Event.event_type == "item.state_changed"
                )
            )
        ).all()
    imported = [e for e in ev if e.source == EventSource.IMPORT]
    assert imported and imported[-1].payload["cause"] == "jira"
    n = len(jira.calls)
    out = await sync.push_item(sf, res["item"]["id"])  # the follow-up push sends nothing back
    assert out.status == "unchanged" and [c for c in jira.calls[n:] if c[0] in ("PUT", "POST")] == []


async def test_done_in_jira_runs_the_stage_sync_and_no_echo_loop_forms(api: Api, jira: FakeJira) -> None:
    secret = await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    key = (await sync.push_item(sf, res["item"]["id"])).key
    assert key
    await hook(api, secret, status_change(key, "Done"))
    item = (await api.call("GET", f"/items/{res['item']['key']}")).json()
    assert item["state"] == "done" and item["closed_at"]
    # Jira then reports the very same status (the echo of a later push): nothing changes
    echo = await hook(api, secret, status_change(key, "Done"))
    assert echo.json()["handled"] == ["status_already"]


async def test_changes_by_the_integration_user_are_ignored(api: Api, jira: FakeJira) -> None:
    secret = await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    key = (await sync.push_item(sf, res["item"]["id"])).key
    assert key
    r = await hook(api, secret, status_change(key, "Done", actor="bot-acc"))
    assert r.json() == {"ignored": "own_change"} and await state_of(api, res["item"]["key"]) == "todo"


async def test_an_unknown_jira_status_creates_a_review_row_and_changes_nothing(
    api: Api, jira: FakeJira
) -> None:
    secret = await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    key = (await sync.push_item(sf, res["item"]["id"])).key
    assert key
    r = await hook(api, secret, status_change(key, "In QA"))
    assert (
        r.json()["handled"] == ["status_unmapped:In QA"] and await state_of(api, res["item"]["key"]) == "todo"
    )
    rows = (await api.call("GET", "/review-queue", params={"kind": "jira_mapping_gap"})).json()["data"]
    assert rows and rows[0]["suggestion"]["jira_status"] == "In QA"
    await hook(api, secret, status_change(key, "In QA"))
    assert (
        len((await api.call("GET", "/review-queue", params={"kind": "jira_mapping_gap"})).json()["data"]) == 1
    )
    fix = await api.call(
        "POST", "/review-queue/resolve", json={"ref": rows[0]["ref"], "choice": {"state": "doing"}}
    )
    assert fix.status_code == 200
    await hook(api, secret, status_change(key, "In QA"))
    assert await state_of(api, res["item"]["key"]) == "doing"


async def test_inbound_comments_are_added_once(
    api: Api, jira: FakeJira, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    secret = await connect(api, auto_send=True)
    res = await new_item(api)
    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    key = (await sync.push_item(sf, res["item"]["id"])).key
    payload = {
        "webhookEvent": "comment_created", "issue": {"key": key},
        "comment": {"id": "9001", "author": {"accountId": "acc-x", "displayName": "Pat", "emailAddress": "pat@ext.test"},
                    "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "looks good"}]}]}},
    }  # fmt: skip
    assert (await hook(api, secret, payload)).json()["handled"] == ["comment"]
    assert (await hook(api, secret, payload)).json() == {"ignored": "duplicate"}
    async with session_factory() as s:
        c = (await s.scalars(select(m.Comment).where(m.Comment.target_id == res["item"]["id"]))).all()
    assert len(c) == 1 and c[0].body == "[Jira · Pat] looks good"


async def test_the_webhook_requires_the_secret_or_a_valid_signature(api: Api, jira: FakeJira) -> None:
    secret = await connect(api, auto_send=True)
    body = b'{"webhookEvent":"jira:issue_created","issue":{"key":"X-1"}}'
    assert (await api.c.post("/api/v1/integrations/jira/webhook/acme", content=body)).status_code == 401
    assert (await hook(api, "wrong", {"webhookEvent": "x"})).status_code == 401
    import hashlib
    import hmac

    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ok = await api.c.post(
        "/api/v1/integrations/jira/webhook/acme",
        content=body,
        headers={"x-hub-signature": sig, "content-type": "application/json"},
    )
    assert ok.status_code == 200 and ok.json() == {"ignored": "event_type"}
    assert (
        await api.c.post("/api/v1/integrations/jira/webhook/nowhere", params={"token": secret}, json={})
    ).status_code == 404


# ---------------------------------------------------------------- linking
async def test_link_existing_by_summary_and_explicit_pairs(api: Api, jira: FakeJira) -> None:
    await connect(api)
    a = await new_item(api, "alpha task")
    b = await new_item(api, "beta task")
    jira.issues["WEB-1"] = {"id": "1", "fields": {"summary": "Alpha Task", "labels": []}, "status": "To Do"}
    jira.issues["WEB-2"] = {"id": "2", "fields": {"summary": "something", "labels": []}, "status": "To Do"}
    r = await api.call(
        "POST", "/projects/WEB/jira:link-existing",
        json={"by_summary": True, "pairs": [{"item": b["item"]["key"], "issue_key": "WEB-2"}, {"item": b["item"]["key"], "issue_key": "WEB-404"}]},
    )  # fmt: skip
    out = r.json()
    assert r.status_code == 200 and {p["issue_key"] for p in out["linked"]} == {"WEB-1", "WEB-2"}
    assert out["skipped"][0]["reason"] in ("already_linked", "not_found_in_jira")
    assert (await api.call("GET", f"/items/{a['item']['key']}")).json()["external_refs"]["jira"] == "WEB-1"


async def test_manual_push_opts_the_item_in(api: Api, jira: FakeJira) -> None:
    await connect(api)
    res = await new_item(api)
    r = await api.call("POST", f"/items/{res['item']['key']}/jira:push")
    assert r.status_code == 200 and r.json()["status"] == "pushed" and r.json()["key"]
    assert (await api.call("POST", f"/items/{res['item']['key']}/jira:push", as_="@ali")).status_code in (
        403,
        404,
    )


def test_jira_pushes_are_only_queued_when_the_feature_is_on() -> None:
    from tt_server.jobs.registry import tasks_for

    feats = get_settings().features
    was = feats.jira_sync
    try:
        feats.jira_sync = False
        assert "jira.on_event" not in tasks_for("item.state_changed")
        feats.jira_sync = True
        for et in ("item.created", "item.state_changed", "time.logged", "stage.moved", "classification.set"):
            assert "jira.on_event" in tasks_for(et), et
        assert "jira.on_event" not in tasks_for("violation.opened")
    finally:
        feats.jira_sync = was


async def test_every_jira_endpoint_is_permission_gated(api: Api, jira: FakeJira) -> None:
    await connect(api)
    res = await new_item(api)
    key = res["item"]["key"]
    for method, path in (
        ("GET", "/integrations/jira"), ("PUT", "/integrations/jira"), ("POST", "/integrations/jira:test"),
        ("POST", "/integrations/jira:rotate-secret"), ("GET", "/integrations/jira/log"),
    ):  # fmt: skip
        r = await api.call(method, path, as_="@ali", json={})
        assert r.status_code == 403, (method, path, r.status_code)
    assert (await api.call("GET", "/projects/WEB/jira", as_="@ali")).status_code in (403, 404)
    assert (await api.call("POST", "/projects/WEB/jira:link-existing", as_="@ali", json={})).status_code in (
        403,
        404,
    )
    assert (await api.call("GET", f"/items/{key}/jira", as_="@ali")).status_code in (403, 404)
    assert (await api.call("POST", "/integrations/jira", json={})).status_code in (404, 405)
