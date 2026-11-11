"""LLM-facing server endpoints and jobs (Phase 2b "Core server additions"; router: llm)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.domain.overlay import suggestions
from tt_server.integrations.llm import GatewayUnavailable, LLMGatewayClient

from tests.api.conftest import Api

ROUTERS_COVERED = ("llm",)


def fake_gateway(handler: Any) -> LLMGatewayClient:
    return LLMGatewayClient(
        "http://gw",
        "t",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw"),
    )


def answer(output: Any, **kw: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "output": output,
            "run_id": "run-42",
            "model": "ollama/x",
            "cost_usd": 0.0,
            "warnings": [],
            **kw,
        },
    )


# ---------------------------------------------------------------- /items/search
async def test_item_search_scores_and_respects_visibility(api: Api) -> None:
    for title, proj in (
        ("OAuth v2 token exchange", "WEB"),
        ("OAuth cookie parser", "WEB"),
        ("Retry refactor", "DATA"),
    ):
        assert (await api.call("POST", "/items", json={"title": title, "project": proj})).status_code == 201
    rows = (await api.call("GET", "/items/search", params={"q": "oauth token"})).json()["data"]
    assert [r["title"] for r in rows][0] == "OAuth v2 token exchange"
    assert all(0 <= r["score"] <= 1 for r in rows) and rows[0]["score"] >= rows[-1]["score"]
    assert {
        r["project_key"]
        for r in (await api.call("GET", "/items/search", params={"q": "retry", "project": "data"})).json()[
            "data"
        ]
    } == {"DATA"}
    assert (await api.call("GET", "/items/search", as_="@ali", params={"q": "oauth"})).json()[
        "data"
    ] == []  # not a member
    mine = (await api.call("GET", "/items/search", params={"q": "oauth", "assignee": "me"})).json()["data"]
    assert isinstance(mine, list)
    assert (await api.call("GET", "/items/search", params={"q": ""})).status_code == 400
    # the static route must not be swallowed by /items/{id_or_key}
    assert (await api.call("GET", "/items/search", params={"q": "zzzz"})).json() == {
        "data": [],
        "next_cursor": None,
    }


# ---------------------------------------------------------------- review items
async def _unplaced(api: Api) -> str:
    await api.send(
        as_="@sara", action="create", item={"title": "fix the billing thing"}, project="the billing thing"
    )
    rows = (await api.call("GET", "/review-queue")).json()["data"]
    row = [r for r in rows if r["kind"] == "unclassified_project"][-1]  # the newest
    return str(row["ref"]).split(":", 1)[1]


async def test_review_item_read_and_patch_belong_to_the_owning_manager(api: Api) -> None:
    rid = await _unplaced(api)
    got = await api.call("GET", f"/review-items/{rid}")
    assert got.status_code == 200 and got.json()["kind"] == "unclassified_project"
    assert (
        await api.call("GET", f"/review-items/{rid}", as_="@ali")
    ).status_code == 403  # not a manager of anyone
    await api.role("@ali", "manager")
    assert (
        await api.call("GET", f"/review-items/{rid}", as_="@ali")
    ).status_code == 404  # a manager, but not the owner
    patched = await api.call(
        "PATCH", f"/review-items/{rid}", json={"suggestion": {"hint": "PAY"}, "confidence": 0.8}
    )
    assert (
        patched.status_code == 200
        and patched.json()["confidence"] == 0.8
        and patched.json()["suggestion"]["hint"] == "PAY"
    )
    assert (await api.call("PATCH", f"/review-items/{rid}", json={"confidence": 2})).status_code == 400
    done = await api.call("PATCH", f"/review-items/{rid}", json={"status": "ignored"})
    assert done.json()["status"] == "resolved"
    assert (await api.call("GET", f"/review-items/{rid}")).status_code == 404  # no longer open
    events = (await api.call("GET", "/events", params={"event_type": "review.updated"})).json()["data"]
    assert len(events) == 2


# ---------------------------------------------------------------- suggested classifications
async def _region(api: Api) -> str:
    assert (
        await api.call(
            "POST", "/dimensions", json={"key": "region", "name": "Region", "is_required_for": {"all": True}}
        )
    ).status_code == 201
    for k in ("emea", "apac"):
        assert (
            await api.call("POST", "/dimensions/region/values", json={"key": k, "name": k.upper()})
        ).status_code == 201
    return "region"


async def test_bulk_suggested_classifications_are_unconfirmed_and_permissioned(api: Api) -> None:
    await _region(api)
    item = (await api.call("POST", "/items", json={"title": "ship to dubai", "project": "WEB"})).json()
    body = {"agent_run_id": "run-7", "suggestions": [
        {"target": item["key"], "dimension": "region", "value": "emea", "confidence": 0.82},
        {"target": item["key"], "dimension": "region", "value": "mars", "confidence": 0.9},
        {"target": "WEB-404", "dimension": "region", "value": "apac", "confidence": 0.9},
    ]}  # fmt: skip
    r = await api.call("POST", "/classifications:suggested", json=body)
    assert r.status_code == 200 and r.json()["written"] == 1 and len(r.json()["skipped"]) == 2
    rows = (
        await api.call("GET", "/classifications", params={"target": item["key"], "dimension": "region"})
    ).json()["data"]
    assert (
        len(rows) == 1
        and rows[0]["is_confirmed"] is False
        and float(rows[0]["confidence"]) == pytest.approx(0.82)
    )
    queue = (await api.call("GET", "/review-queue")).json()["data"]
    assert any(
        q["kind"] == "unconfirmed_classification" and q["suggestion"]["value"] == "emea" for q in queue
    )
    ev = (await api.call("GET", "/events", params={"event_type": "classification.changed"})).json()["data"]
    assert any((e["payload"] or {}).get("agent_run_id") == "run-7" for e in ev)
    assert (await api.call("POST", "/classifications:suggested", as_="@sara", json=body)).status_code == 403
    # a manual classification is never overwritten by a suggestion
    await api.call(
        "POST",
        "/classifications",
        json={"target_type": "work_item", "target_id": item["key"], "dimension": "region", "value": "apac"},
    )
    again = await api.call(
        "POST", "/classifications:suggested", json={"suggestions": [body["suggestions"][0]]}
    )
    assert again.json()["written"] == 0 and "manual" in again.json()["skipped"][0]["reason"]
    final = (
        await api.call(
            "GET",
            "/classifications",
            params={"target": item["key"], "dimension": "region", "confirmed": "true"},
        )
    ).json()["data"]
    assert [c["value"] for c in final] == ["apac"]


# ---------------------------------------------------------------- service tokens + agent run id
async def test_service_token_is_admin_only_and_read_only(api: Api) -> None:
    assert (await api.call("POST", "/auth/service-tokens", as_="@ali", json={})).status_code == 403
    r = await api.call("POST", "/auth/service-tokens", json={"name": "gw"})
    assert r.status_code == 201 and r.json()["scopes"] == ["capture", "read"]
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await api.c.get(api.url("/items/search"), params={"q": "x"}, headers=hdr)).status_code == 200
    assert (
        await api.c.post(api.url("/items"), headers=hdr, json={"title": "x", "project": "WEB"})
    ).status_code == 403


async def test_agent_run_id_header_lands_on_every_event_of_the_batch(api: Api) -> None:
    body = {"workspace": "acme", "messages": [
        {"actor": "@mahmoud", "action": "create", "at": "2026-10-23T10:00:00Z", "client_id": "agent-1", "item": {"title": "via the gateway"}, "project": "WEB"}
    ]}  # fmt: skip
    r = await api.c.post(
        api.url("/capture/batches"),
        json=body,
        headers={"X-Debug-Actor": "@mahmoud", "X-Agent-Run-Id": "run-99"},
    )
    assert r.status_code == 200
    events = (await api.call("GET", "/events", params={"limit": 50})).json()["data"]
    created = [
        e
        for e in events
        if e["event_type"] == "item.created" and e["payload"]["after"].get("title") == "via the gateway"
    ] or [e for e in events if e["event_type"] == "item.created"]
    assert created and all(e["payload"].get("agent_run_id") == "run-99" for e in created)
    plain = await api.send(action="create", item={"title": "no agent"}, project="WEB")
    ev2 = [
        e
        for e in (await api.call("GET", "/events", params={"entity_id": plain["item"]["id"]})).json()["data"]
    ]
    assert ev2 and all("agent_run_id" not in e["payload"] for e in ev2)


# ---------------------------------------------------------------- suggestion jobs against a fake gateway
async def test_review_suggestions_are_written_once_with_an_event(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    rid = await _unplaced(api)
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        seen.append(body)
        return answer(
            {"suggestions": [{"choice": {"project": "WEB"}, "confidence": 0.86, "why": "billing page"}]},
            confidence=0.86,
        )

    client = fake_gateway(handler)
    async with session_factory() as s:
        ws_id = str(await s.scalar(select(m.Workspace.id)))
    res = await suggestions.run_review_suggestions(session_factory, client, ws_id)
    assert (res.attempted, res.written) == (1, 1)
    assert (
        seen[0]["row"]["kind"] == "unclassified_project"
        and "WEB" in seen[0]["candidates"]["projects"]
        and "INBOX-sara" not in str(seen[0]["candidates"])
    )
    row = (await api.call("GET", f"/review-items/{rid}")).json()
    assert row["suggestion"]["llm"]["suggestions"][0]["choice"] == {"project": "WEB"} and row[
        "confidence"
    ] == pytest.approx(0.86)
    assert row["suggestion"]["phrase"] == "the billing thing"  # the original suggestion is preserved
    ev = (await api.call("GET", "/events", params={"event_type": "agent.suggestion_written"})).json()["data"]
    assert len(ev) == 1 and ev[0]["payload"]["agent_run_id"] == "run-42"
    again = await suggestions.run_review_suggestions(session_factory, client, ws_id)
    assert again.attempted == 0 and len(seen) == 1  # never asked twice
    # the manager still decides: resolving uses the choice the manager picks
    ok = await api.call(
        "POST", "/review-queue/resolve", json={"ref": f"review_item:{rid}", "choice": {"project": "WEB"}}
    )
    assert ok.status_code == 200


async def test_low_confidence_and_gateway_failures_leave_the_queue_untouched(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    rid = await _unplaced(api)
    async with session_factory() as s:
        ws_id = str(await s.scalar(select(m.Workspace.id)))
    weak = fake_gateway(
        lambda r: answer({"suggestions": [{"choice": {"project": "WEB"}, "confidence": 0.2}]})
    )
    assert (await suggestions.run_review_suggestions(session_factory, weak, ws_id)).written == 0
    assert (await api.call("GET", f"/review-items/{rid}")).json()["suggestion"]["llm"]["none"] is True
    rid2 = await _unplaced(api)
    for status in (500, 429):
        broken = fake_gateway(lambda r, s=status: httpx.Response(s, headers={"Retry-After": "60"}, json={}))
        res = await suggestions.run_review_suggestions(session_factory, broken, ws_id)
        assert res.written == 0 and res.errors == ["gateway unavailable"]
    assert "llm" not in (await api.call("GET", f"/review-items/{rid2}")).json()["suggestion"]
    down = fake_gateway(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(GatewayUnavailable):
        await down.suggest_review("acme", {}, {})


async def test_classification_prefill_creates_unconfirmed_rows(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _region(api)
    a = (await api.call("POST", "/items", json={"title": "launch in riyadh", "project": "WEB"})).json()
    b = (await api.call("POST", "/items", json={"title": "launch in tokyo", "project": "WEB"})).json()

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        assert {i["key"] for i in body["items"]} >= {a["key"], b["key"]} and body["dimensions"][0][
            "key"
        ] == "region"
        return answer({"items": [
            {"key": a["key"], "dimension": "region", "value": "emea", "confidence": 0.9},
            {"key": b["key"], "dimension": "region", "value": "apac", "confidence": 0.3},
        ]})  # fmt: skip

    async with session_factory() as s:
        ws_id = str(await s.scalar(select(m.Workspace.id)))
    res = await suggestions.prefill_classifications(session_factory, fake_gateway(handler), ws_id)
    assert res.written == 1 and res.skipped == 1
    rows = (await api.call("GET", "/classifications", params={"dimension": "region"})).json()["data"]
    assert [(r["is_confirmed"], r["source"]) for r in rows] == [(False, "rule")]
    assert (await api.call("GET", "/events", params={"event_type": "agent.suggestion_written"})).json()[
        "data"
    ]


async def test_run_now_endpoints_need_admin_and_a_configured_gateway(api: Api) -> None:
    assert (await api.call("POST", "/llm/suggest-reviews")).status_code == 409
    assert (await api.call("POST", "/llm/prefill-classifications", as_="@ali")).status_code == 403
    api.c._transport.app.state.llm = fake_gateway(lambda r: answer({"suggestions": []}))  # type: ignore[attr-defined]
    await _unplaced(api)
    r = await api.call("POST", "/llm/suggest-reviews")
    assert r.status_code == 200 and r.json()["attempted"] == 1
    assert (await api.call("POST", "/llm/prefill-classifications")).json()["attempted"] == 0


def test_llm_jobs_are_only_enqueued_when_a_gateway_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from tt_server import settings as st
    from tt_server.jobs.registry import tasks_for

    st.get_settings.cache_clear()
    monkeypatch.delenv("TT_LLM_GATEWAY_URL", raising=False)
    assert tasks_for("review.created") == []
    monkeypatch.setenv("TT_LLM_GATEWAY_URL", "http://gw:8700")
    monkeypatch.setenv("TT_FEATURES__LLM_SUGGESTIONS", "true")
    st.get_settings.cache_clear()
    assert tasks_for("review.created") == ["llm.suggest_for_event"]
    st.get_settings.cache_clear()
