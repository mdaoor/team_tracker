"""The optional gateway assist for personal quick-add: refines undecided kinds only; failures fall back to the parser."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import NOW
from tt_client.parser.personal import parse_personal
from tt_client.parser.personal_llm import apply_assist, fetch
from tt_client.parser.structured import ParseContext
from tt_client.personal.model import Category, Kind
from tt_client.personal.service import Tracker

CTX = ParseContext(actor="@me", now=NOW, tz="Asia/Riyadh")


def test_an_undecided_line_stays_private_and_the_models_work_reading_is_only_a_question() -> None:
    (d,) = parse_personal("sort out the offsite 1h", CTX)
    assert (d.kind, d.category, d.kind_source) == (Kind.OTHER, Category.PERSONAL, "default")
    (out,) = apply_assist([d], [{"kind": "task", "category": "work", "project_hint": "WEB"}])
    assert out.category == Category.PERSONAL and out.project_hint is None  # nothing is applied
    assert (out.suggested_kind, out.suggested_category) == (Kind.TASK, Category.WORK)
    assert out.start == d.start and out.duration_min == 60


def test_an_undecided_line_can_take_a_kind_that_is_not_less_private() -> None:
    (d,) = parse_personal("tea with mum at 8am 45m", CTX)
    (out,) = apply_assist([d], [{"kind": "habit", "category": "personal"}])
    assert (out.kind, out.category, out.kind_source) == (Kind.HABIT, Category.PERSONAL, "label")


def test_a_decided_line_is_only_ever_made_more_private() -> None:
    (d,) = parse_personal("standup 15m at 9am", CTX)
    assert d.kind_source == "keyword" and d.category == Category.MEETING
    assert apply_assist([d], [{"kind": "task", "category": "work"}])[0].category == Category.MEETING
    assert apply_assist([d], [{"kind": "task", "category": "personal"}])[0].category == Category.PERSONAL
    (w,) = parse_personal("fix login 1h", CTX)
    assert (
        apply_assist([w], [{"kind": "task", "category": "work", "project_hint": "web"}])[0].project_hint
        == "WEB"
    )


def test_garbage_or_mismatched_answers_change_nothing() -> None:
    drafts = parse_personal("fix login 1h", CTX)
    assert apply_assist(drafts, None) == drafts and apply_assist(drafts, []) == drafts
    assert apply_assist(drafts, [{"kind": "nonsense", "category": "work"}]) == drafts
    assert (
        apply_assist(drafts, [{"kind": "task", "category": "work"}, {"kind": "task", "category": "work"}])
        == drafts
    )


def test_fetch_posts_only_the_line_and_project_keys_and_survives_every_failure() -> None:
    seen: list[httpx.Request] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"output": {"drafts": [{"kind": "other", "category": "personal"}]}})

    http = httpx.Client(transport=httpx.MockTransport(ok))
    got = fetch(
        "http://gw",
        "tea 20m",
        actor="@me",
        now=NOW.isoformat(),
        time_zone="UTC",
        projects=[{"key": "WEB"}],
        client=http,
    )
    assert got == [{"kind": "other", "category": "personal"}]
    body = json.loads(seen[0].content)
    assert body["pack"] == {"projects": [{"key": "WEB"}]} and "authorization" not in seen[0].headers
    assert seen[0].url.path == "/v1/translate/personal"
    for bad in (
        httpx.Response(503, json={}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"nope": 1}),
    ):
        assert (
            fetch(
                "http://gw",
                "x",
                actor="@me",
                now="",
                time_zone="UTC",
                projects=[],
                client=httpx.Client(transport=httpx.MockTransport(lambda r, b=bad: b)),
            )
            is None
        )

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no gateway")

    assert (
        fetch(
            "http://gw",
            "x",
            actor="@me",
            now="",
            time_zone="UTC",
            projects=[],
            client=httpx.Client(transport=httpx.MockTransport(down)),
        )
        is None
    )


def test_tracker_add_uses_the_assist_callable(tracker: Tracker) -> None:
    tracker.settings.set("privacy.assist", "on")
    (item,) = tracker.add(
        "sort out the offsite 1h", assist=lambda line: [{"kind": "task", "category": "work"}]
    )
    # a less private reading is only ever a suggestion for the person to confirm, never applied
    assert item.category == Category.PERSONAL
    (plain,) = tracker.add("sort out the offsite 1h", assist=lambda line: None)
    assert (
        plain.category == Category.PERSONAL
    )  # without help an unrecognised line is private; the person can recategorise it


def test_the_assist_callable_is_never_called_unless_assist_is_on_and_the_parser_left_a_kind_undecided(
    tracker: Tracker,
) -> None:
    calls: list[str] = []

    def assist(line: str) -> list[dict[str, str]] | None:
        calls.append(line)
        return None

    assert tracker.settings.get("privacy.assist") == "off"
    tracker.add("sort out the offsite 1h", assist=assist)
    assert calls == []  # off by default
    tracker.settings.set("privacy.assist", "on")
    tracker.add("run 30m at 6pm", assist=assist)
    assert calls == []  # decided by the deterministic parser
    tracker.add("sort out the offsite 1h", assist=assist)
    assert calls == ["sort out the offsite 1h"]
    with pytest.raises(ValueError):
        tracker.settings.set("privacy.assist", "maybe")
