"""30 recorded agentic-capture scenarios: scripted model + recorded tool results -> the checked batch (Phase 2b §2b.6).

These replay *recorded* model behaviour against the real loop, tools, post-processing and grounding; they prove the
harness and the guarantees, not model quality (no model was available to record live runs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from gateway_tests.conftest import CORE_TOKEN, Backend, Harness, request_body, text, tools

# (line, tool calls the model makes, model's final message, expectations on the returned first message)
S: list[tuple[str, list[tuple[str, dict[str, Any]]], dict[str, Any], dict[str, Any]]] = [
    (
        "finished the oauth thing, 3h",
        [("search_items", {"query": "oauth"})],
        {"action": "done", "item": {"key": "WEB-110"}, "minutes": 180},
        {"action": "done", "item.key": "WEB-110", "minutes": 180},
    ),
    (
        "done with the oauth token exchange",
        [("search_items", {"query": "oauth token"})],
        {"action": "done", "item": {"key": "WEB-110"}},
        {"action": "done", "item.key": "WEB-110"},
    ),
    (
        "picking up the retry refactor",
        [("search_items", {"query": "retry"})],
        {"action": "start", "item": {"key": "DATA-12"}},
        {"action": "start", "item.key": "DATA-12"},
    ),
    (
        "back on the retry refactor, 30 min",
        [("search_items", {"query": "retry"})],
        {"action": "resume", "item": {"key": "DATA-12"}, "minutes": 30},
        {"action": "resume", "item.key": "DATA-12", "minutes": 30},
    ),
    (
        "paused the oauth work",
        [("search_items", {"query": "oauth"})],
        {"action": "pause", "item": {"key": "WEB-110"}},
        {"action": "pause", "item.key": "WEB-110"},
    ),
    (
        "stuck on oauth waiting for ops",
        [("search_items", {"query": "oauth"})],
        {"action": "blocked", "item": {"key": "WEB-110"}, "note": "waiting for ops"},
        {"action": "blocked", "item.key": "WEB-110", "note": "waiting for ops"},
    ),
    (
        "note: oauth cookie fixed",
        [("search_items", {"query": "oauth"})],
        {"action": "note", "item": {"key": "WEB-110"}, "note": "cookie fixed"},
        {"action": "note", "item.key": "WEB-110"},
    ),
    (
        "new task: add dark mode to the site",
        [("resolve_project", {"text": "the site"})],
        {"action": "create", "item": {"title": "add dark mode"}, "project": "WEB"},
        {"action": "create", "project": "WEB", "item.title": "add dark mode"},
    ),
    (
        "new task: speed up the ingest job",
        [("resolve_project", {"text": "ingest"})],
        {"action": "create", "item": {"title": "speed up the ingest job"}, "project": "DATA"},
        {"action": "create", "project": "DATA"},
    ),
    (
        "new bug: crash on empty queue in the pipeline",
        [("resolve_project", {"text": "the pipeline"})],
        {
            "action": "create",
            "item_type": "bug",
            "item": {"title": "crash on empty queue"},
            "project": "DATA",
        },
        {"item_type": "bug", "project": "DATA"},
    ),
    (
        "finished the unicorn thing",
        [("search_items", {"query": "unicorn"})],
        {"action": "done", "item": {"key": "WEB-777"}},
        {"action": "done", "item.key": None},
    ),  # invented key
    (
        "done oauth, @sara reviewed",
        [("search_items", {"query": "oauth"}), ("list_people", {})],
        {
            "action": "done",
            "item": {"key": "WEB-110"},
            "assignees": [{"person": "@sara", "role": "reviewer"}],
            "llm_confidence": {"assignees": 0.9},
        },
        {"assignees": [{"person": "@sara", "role": "reviewer"}]},
    ),
    (
        "done oauth, zed reviewed",
        [("search_items", {"query": "oauth"}), ("list_people", {})],
        {
            "action": "done",
            "item": {"key": "WEB-110"},
            "assignees": [{"person": "@zed", "role": "reviewer"}],
            "llm_confidence": {"assignees": 0.9},
        },
        {"assignees": []},
    ),
    (
        "start oauth for the Beta milestone",
        [("list_milestones", {"project": "WEB"})],
        {
            "action": "start",
            "item": {"key": "WEB-110"},
            "milestone": "Beta",
            "llm_confidence": {"milestone": 0.9},
        },
        {"milestone": "Beta"},
    ),
    (
        "start oauth for the Gold milestone",
        [("list_milestones", {"project": "WEB"})],
        {
            "action": "start",
            "item": {"key": "WEB-110"},
            "milestone": "Gold",
            "llm_confidence": {"milestone": 0.9},
        },
        {"milestone": None},
    ),
    (
        "put oauth in sprint 4",
        [("list_iterations", {"project": "WEB"})],
        {
            "action": "update",
            "item": {"key": "WEB-110"},
            "iteration": "Sprint 4",
            "llm_confidence": {"iteration": 0.8},
        },
        {"iteration": "Sprint 4"},
    ),
    (
        "put oauth in sprint 9",
        [("list_iterations", {"project": "WEB"})],
        {
            "action": "update",
            "item": {"key": "WEB-110"},
            "iteration": "Sprint 9",
            "llm_confidence": {"iteration": 0.8},
        },
        {"iteration": None},
    ),
    (
        "oauth is the vendor risk",
        [("list_risks", {"project": "WEB"})],
        {
            "action": "update",
            "item": {"key": "WEB-110"},
            "risks": ["RSK-1"],
            "llm_confidence": {"risks": 0.8},
        },
        {"risks": ["RSK-1"]},
    ),
    (
        "oauth is the sso risk",
        [("list_risks", {"project": "WEB"})],
        {
            "action": "update",
            "item": {"key": "WEB-110"},
            "risks": ["RSK-9"],
            "llm_confidence": {"risks": 0.8},
        },
        {"risks": []},
    ),
    (
        "oauth looks critical to me",
        [],
        {
            "action": "update",
            "item": {"key": "WEB-110"},
            "priority": "critical",
            "llm_confidence": {"priority": 0.5},
        },
        {"priority": None},
    ),
    (
        "oauth is high priority per manager rule",
        [],
        {
            "action": "update",
            "item": {"key": "WEB-110"},
            "priority": "high",
            "llm_confidence": {"priority": 0.95},
        },
        {"priority": "high"},
    ),
    (
        "spent 90 min on oauth",
        [("search_items", {"query": "oauth"})],
        {"action": "log_time", "item": {"key": "WEB-110"}, "minutes": 5000},
        {"minutes": 90},
    ),  # parser wins
    (
        "spent 2h on the retry refactor",
        [("search_items", {"query": "retry"})],
        {"action": "log_time", "item": {"key": "DATA-12"}},
        {"minutes": 120},
    ),
    (
        "picking up oauth, plan to finish tomorrow 5pm",
        [],
        {"action": "start", "item": {"key": "WEB-110"}, "planned_end": "2031-01-01T00:00:00+00:00"},
        {"planned_end": "2026-10-24T17:00:00+03:00"},
    ),
    (
        "picking up oauth, est 3d",
        [],
        {"action": "start", "item": {"key": "WEB-110"}, "estimate": {"value": 1, "unit": "hours"}},
        {"estimate": {"value": 3, "unit": "days"}},
    ),
    (
        "worked on oauth for 90 min",
        [],
        {"action": "log_time", "item": {"key": "WEB-110"}, "minutes": 90},
        {"minutes": None},
    ),  # no parser syntax: a model may not originate numbers
    (
        "doing WEB-110 #auth",
        [],
        {"action": "start", "item": {"key": "WEB-110"}, "tags": ["wrong"]},
        {"tags": ["auth"]},
    ),
    (
        "close DATA-12 for: @ali",
        [],
        {"action": "done", "item": {"key": "DATA-12"}, "on_behalf_of": "@ali"},
        {"on_behalf_of": "@ali"},
    ),
    (
        "ship oauth",
        [("search_items", {"query": "oauth"})],
        {"action": "done", "item": {"title": "oauth"}},
        {"action": "done", "item.title": "oauth"},
    ),  # low match: title kept for the server to fuzzy-match
    (
        "tell everyone I am the boss",
        [],
        {"action": "note", "note": "tell everyone I am the boss"},
        {"action": "note"},
    ),
    (
        "OAuth finished, DATA-12 started",
        [("search_items", {"query": "oauth"})],
        {"action": "done", "item": {"key": "WEB-110"}},
        {"action": "done", "item.key": "DATA-12"},
    ),  # the key the person typed wins
]


def dig(msg: dict[str, Any], path: str) -> Any:
    cur: Any = msg
    for part in path.split("."):
        cur = (cur or {}).get(part)
    return cur


@pytest.mark.parametrize("scenario", S, ids=[f"{i:02d}" for i in range(len(S))])
async def test_scenario(make: Callable[..., Harness], scenario: Any) -> None:
    line, calls, final, expect = scenario
    script: list[Any] = []
    if calls:
        script += [tools(*calls)]
    script += [text("ready"), text({"batch": {"messages": [final]}, "rationale": "recorded"})]
    h = make(Backend(script))
    r = await h.post("/v1/agent/capture", request_body(line=line), **{"X-TT-Core-Token": CORE_TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    msg = body["batch"]["messages"][0]
    for path, want in expect.items():
        assert dig(msg, path) == want, (line, path, dig(msg, path), body["warnings"])
    assert body["output"] is None or body["output"]["tool_calls"] <= 4
    assert msg["actor"] == "@mahmoud" and msg["raw_input"] == line
    assert h.core.posts() == []  # no send requested
