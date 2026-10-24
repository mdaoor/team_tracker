"""Gateway <-> core server over real HTTP (Phase 2b acceptance): agentic capture with tools, send=true, run id, Jira status."""

from __future__ import annotations

import json
from typing import Any

import httpx
from tt_llm.app import create_app as create_gateway
from tt_llm.config import default_config
from tt_llm.routing import Completion, ToolCall

from tests.e2e.conftest import Live


def text(obj: Any) -> Completion:
    return Completion(content=json.dumps(obj), model="ollama/e2e")


def call(name: str, **args: Any) -> Completion:
    return Completion(content=None, tool_calls=[ToolCall("c1", name, args)], model="ollama/e2e")


class Script:
    def __init__(self, steps: list[Completion]) -> None:
        self.steps, self.seen = steps, []

    async def __call__(self, **kw: Any) -> Completion:
        self.seen.append(kw)
        return self.steps.pop(0)


async def test_agent_capture_uses_real_search_then_sends_and_tags_events(
    live: Live, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TT_LLM_GATEWAY_TOKENS", "gw")
    base = f"{live.url}/api/v1/w/{live.slug}"
    mh = {"Authorization": f"Bearer {live.manage_token}"}
    async with httpx.AsyncClient(timeout=10) as core:
        item = await core.post(
            f"{base}/items", headers=mh, json={"title": "OAuth v2 token exchange", "project": "WEB"}
        )
    assert item.status_code == 201, item.text
    key = item.json()["key"]

    final = {
        "batch": {
            "messages": [
                {
                    "action": "done",
                    "item": {"key": key},
                    "minutes": 999,
                    "llm_confidence": {"item": 0.9},
                    "jira": {"project": "WEB", "issuetype": "Task", "send": True},
                }
            ]
        },
        "rationale": "title similarity",
    }
    backend = Script([call("search_items", query="oauth token"), text("ready"), text(final)])
    cfg = default_config(privacy_mode="cloud", server={"base_url": live.url})
    app = create_gateway(cfg, backend=backend)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw", headers={"Authorization": "Bearer gw"}
    ) as gw:
        r = await gw.post(
            "/v1/agent/capture",
            json={
                "workspace": live.slug,
                "actor": live.handle,
                "line": "finished the oauth thing, 3h",
                "options": {"send": True},
                "time_zone": "Asia/Riyadh",
            },
            headers={"X-TT-Core-Token": live.token},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # the tool call went to the real server and its hit grounded the key the model returned
    tool_msgs = [m for m in backend.seen[-1]["messages"] if m["role"] == "tool"]
    assert key in tool_msgs[0]["content"] and "score" in tool_msgs[0]["content"]
    msg = body["batch"]["messages"][0]
    assert msg["item"]["key"] == key and msg["minutes"] == 180  # the parser's 3h, not the model's 999
    server = body["output"]["server"]
    assert server["results"][0]["status"] in ("applied", "deduplicated") and server["results"][0]["jira"] == {
        "status": "deferred"
    }
    # every event written for that batch carries the gateway run id
    async with httpx.AsyncClient(timeout=10) as core:
        events = (await core.get(f"{base}/events", headers=mh, params={"limit": 100})).json()["data"]
    mine = [e for e in events if e["payload"].get("agent_run_id") == body["run_id"]]
    assert "time.logged" in {e["event_type"] for e in mine}
    assert all(e["payload"]["agent_run_id"] == body["run_id"] for e in mine)


async def test_a_capture_scoped_token_can_drive_the_agent_but_a_missing_token_cannot_send(
    live: Live, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TT_LLM_GATEWAY_TOKENS", "gw")
    cfg = default_config(privacy_mode="cloud", server={"base_url": live.url})
    steps = [
        text("ready"),
        text({"batch": {"messages": [{"action": "note", "note": "hello", "project": "WEB"}]}}),
    ]
    app = create_gateway(cfg, backend=Script(steps))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gw", headers={"Authorization": "Bearer gw"}
    ) as gw:
        body = {
            "workspace": live.slug,
            "actor": live.handle,
            "line": "note: hello",
            "options": {"send": True},
        }
        assert (await gw.post("/v1/agent/capture", json=body)).status_code == 400
        ok = await gw.post(
            "/v1/agent/capture", json=body, headers={"X-TT-Core-Token": live.token}
        )  # `capture`+`read` scopes suffice
    assert ok.status_code == 200 and ok.json()["output"]["server"]["results"][0]["status"] in (
        "applied",
        "deduplicated",
    )
