"""Agentic capture (Phase 2b §2b.3): tools, trace, send=true, limits, injection resistance."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gateway_tests.conftest import CORE_TOKEN, Backend, Harness, request_body, text, tools

OAUTH_LINE = "finished the oauth thing, 3h, @sara reviewed it"


def final(**msg: Any) -> Completion:  # noqa: F821
    base = {"action": "done", "item": {"key": "WEB-110"}, "minutes": 180, "assignees": [{"person": "@sara", "role": "reviewer"}],
            "llm_confidence": {"item": 0.93, "assignees": 0.9}}  # fmt: skip
    return text(
        {"batch": {"messages": [{**base, **msg}]}, "rationale": "Matched WEB-110 by title similarity."}
    )


from tt_llm.routing import Completion  # noqa: E402


def oauth_script() -> list[Any]:
    return [
        tools(("get_context_pack", {})),
        tools(("search_items", {"query": "oauth", "assignee": "me"}), ("list_people", {})),
        text("done"),  # no more tools
        final(),
    ]


async def test_agent_resolves_the_oauth_line_with_tools_and_a_short_trace(
    make: Callable[..., Harness],
) -> None:
    h = make(Backend(oauth_script()))
    r = await h.post("/v1/agent/capture", request_body(line=OAUTH_LINE), **{"X-TT-Core-Token": CORE_TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    msg = body["batch"]["messages"][0]
    assert msg["item"]["key"] == "WEB-110" and msg["minutes"] == 180 and msg["action"] == "done"
    assert msg["assignees"] == [{"person": "@sara", "role": "reviewer"}]
    assert msg["actor"] == "@mahmoud" and msg["client_id"]  # identity filled by code
    assert body["output"]["tool_calls"] == 3 and body["rationale"].startswith("Matched")
    # the pack was supplied, so get_context_pack cost no network call; search went to the core server
    assert [r.url.path.rsplit("/", 1)[-1] for r in h.core.requests] == ["search"]
    run = await h.storage.get_run(body["run_id"])
    assert run is not None and run.status == "ok"
    assert [s.name for s in run.steps if s.kind == "tool"] == [
        "get_context_pack",
        "search_items",
        "list_people",
    ]
    assert run.meta["rationale"].startswith("Matched")  # stored in agent_runs, ...
    assert "rationale" not in str(h.core.posts())  # ... never sent to the server


async def test_send_true_posts_through_the_capture_api_with_the_run_id(make: Callable[..., Harness]) -> None:
    h = make(Backend(oauth_script()))
    r = await h.post(
        "/v1/agent/capture",
        request_body(line=OAUTH_LINE, options={"send": True}),
        **{"X-TT-Core-Token": CORE_TOKEN},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    (post,) = h.core.posts()
    assert post.url.path.endswith("/capture/batches") and post.headers["x-agent-run-id"] == body["run_id"]
    assert (
        post.headers["authorization"] == f"Bearer {CORE_TOKEN}"
    )  # the caller's own token, not a gateway one
    assert body["output"]["server"]["results"][0]["jira"] == {"status": "deferred"}
    # without send=true nothing is ever posted
    h2 = make(Backend(oauth_script()))
    await h2.post("/v1/agent/capture", request_body(line=OAUTH_LINE), **{"X-TT-Core-Token": CORE_TOKEN})
    assert len(h2.core.posts()) == 1  # still only the one post from above (shared fake core)


async def test_send_needs_the_callers_core_token_and_reports_a_refusal(make: Callable[..., Harness]) -> None:
    h = make(Backend(oauth_script()))
    r = await h.post("/v1/agent/capture", request_body(line=OAUTH_LINE, options={"send": True}))
    assert r.status_code == 400 and r.json()["error"]["code"] == "core_token_required"
    h.core.post_status = 422
    h2 = make(Backend(oauth_script()))
    h2.core.post_status = 422
    ok = await h2.post(
        "/v1/agent/capture",
        request_body(line=OAUTH_LINE, options={"send": True}),
        **{"X-TT-Core-Token": CORE_TOKEN},
    )
    assert ok.status_code == 200  # the batch is still returned for the client's outbox


async def test_the_model_never_gets_write_tools_and_blocked_calls_are_refused(
    make: Callable[..., Harness],
) -> None:
    sneaky = [
        tools(
            ("post_capture_batch", {"batch": {"workspace": "acme", "messages": []}}),
            ("resolve_review_item", {"ref": "review_item:1"}),
        ),
        tools(("get_item", {"key": "WEB-110"})),
        text("ok"),
        final(),
    ]
    b = Backend(sneaky)
    h = make(b)
    r = await h.post("/v1/agent/capture", request_body(line=OAUTH_LINE), **{"X-TT-Core-Token": CORE_TOKEN})
    assert r.status_code == 200
    offered = {t["function"]["name"] for t in b.calls[0]["tools"]}
    assert not offered & {"post_capture_batch", "resolve_review_item", "create_classification"}
    assert h.core.posts() == []  # the blocked write calls never reached the core server
    assert any("blocked call to post_capture_batch" in w for w in r.json()["warnings"])
    run = await h.storage.get_run(r.json()["run_id"])
    assert run is not None
    blocked = [s for s in run.steps if s.kind == "tool" and s.name == "post_capture_batch"]
    assert blocked and blocked[0].output["error"] == "tool_not_allowed"


async def test_invalid_tool_arguments_and_tool_budget(make: Callable[..., Harness]) -> None:
    calls = [tools(("search_items", {"nope": 1}))]  # missing required `query`, extra key
    calls += [tools(*[("get_item", {"key": f"WEB-{i}"}) for i in range(5)]) for _ in range(3)]
    calls += [text("enough"), final()]
    h = make(Backend(calls), limits={"max_tool_calls": 6})
    r = await h.post("/v1/agent/capture", request_body(line=OAUTH_LINE), **{"X-TT-Core-Token": CORE_TOKEN})
    assert r.status_code == 200
    run = await h.storage.get_run(r.json()["run_id"])
    assert run is not None
    assert run.steps[1].output["error"] == "invalid_arguments"
    assert any("tool call budget exhausted" in w for w in r.json()["warnings"])
    assert len([s for s in run.steps if s.kind == "tool" and "error" not in (s.output or {})]) <= 5


async def test_max_steps_still_forces_a_final_answer(make: Callable[..., Harness]) -> None:
    endless = Backend([tools(("list_people", {}))] * 3 + [final()])
    h = make(endless, limits={"max_steps": 3})
    r = await h.post("/v1/agent/capture", request_body(line=OAUTH_LINE), **{"X-TT-Core-Token": CORE_TOKEN})
    assert r.status_code == 200 and r.json()["batch"]["messages"][0]["item"]["key"] == "WEB-110"
    assert len(endless.calls) == 4  # 3 loop steps + the forced final answer


async def test_large_tool_results_are_truncated_explicitly(make: Callable[..., Harness]) -> None:
    h = make(
        Backend([tools(("search_items", {"query": "oauth"})), text("k"), final()]),
        limits={"tool_result_max_chars": 200},
    )
    h.core.items = [
        {
            "key": f"WEB-{i}",
            "title": "oauth " + "x" * 40,
            "project_key": "WEB",
            "state_category": "doing",
            "score": 0.5,
        }
        for i in range(30)
    ]
    r = await h.post("/v1/agent/capture", request_body(line=OAUTH_LINE), **{"X-TT-Core-Token": CORE_TOKEN})
    run = await h.storage.get_run(r.json()["run_id"])
    assert (
        run is not None and run.steps[1].output["truncated"] is True and len(run.steps[1].output["data"]) < 30
    )


async def test_multi_line_capture_returns_one_message_per_line(make: Callable[..., Harness]) -> None:
    out = text(
        {
            "batch": {
                "messages": [
                    {"action": "done", "item": {"key": "WEB-110"}, "minutes": 60},
                    {"action": "start", "item": {"title": "write the retry tests"}},
                ]
            }
        }
    )
    h = make(Backend([text("done"), out]))
    r = await h.post(
        "/v1/agent/capture",
        request_body(lines=["finished WEB-110, took 1h", "doing write the retry tests"]),
        **{"X-TT-Core-Token": CORE_TOKEN},
    )
    msgs = r.json()["batch"]["messages"]
    assert [m["action"] for m in msgs] == ["done", "start"] and msgs[0]["minutes"] == 60
    assert (
        msgs[0]["raw_input"] == "finished WEB-110, took 1h"
        and msgs[1]["raw_input"] == "doing write the retry tests"
    )
