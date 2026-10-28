"""Phase 13 R2.2 / R6.2 / R6.7: capture intent, honest cues, end-of-day nudge through the notification port."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tt_client.notify import Notification, NudgeLog, end_of_day_due, end_of_day_nudge
from tt_client.pack import ContextPack
from tt_client.parser.structured import ParseContext, StructuredParser
from tt_client.preview import build_preview
from tt_schema.client import ClientAction

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PACK = ContextPack(
    {
        "projects": [{"key": "WEB", "name": "Website", "aliases": []}],
        "my_open_items": [{"key": "WEB-114", "title": "ingestion retry", "project": "WEB", "stage": None}],
        "my_current_activity": {"key": "WEB-114", "title": "ingestion retry"},
        "people": [{"handle": "@sara"}],
    }
)


def parse(line: str, **kw: Any) -> Any:
    return StructuredParser(ParseContext(actor="@sara", now=NOW, tz="Asia/Riyadh", **kw)).parse(line)


def test_a_verbless_noun_phrase_creates_even_while_something_is_running() -> None:
    (msg,) = parse("refactor the ingestion retry p:ingestion #retry-logic").messages
    assert msg.action == ClientAction.CREATE and msg.item and msg.item.title == "refactor the ingestion retry"
    assert msg.note is None  # never glued onto the active item (UX review 4)


@pytest.mark.parametrize("line", ["the retry is flaky", "need to ask ops", "it keeps timing out"])
def test_observations_stay_notes(line: str) -> None:
    (msg,) = parse(line).messages
    assert msg.action == ClientAction.NOTE and msg.note == line


def test_text_about_a_named_key_is_a_note_on_that_key() -> None:
    (msg,) = parse("WEB-114 looks fine now").messages
    assert msg.action == ClientAction.NOTE and msg.item and msg.item.key == "WEB-114"


def test_very_short_phrases_are_flagged_ambiguous_and_the_card_asks() -> None:
    res = parse("glorp")
    (msg,) = res.messages
    assert msg.action == ClientAction.CREATE and (msg.llm_confidence or {}).get("intent")
    assert any("new task or a note" in n for n in res.notes)
    rows = {r.label: r for r in build_preview(msg, PACK)}
    assert "ask" in rows["Task"].cue


def test_unknown_handles_stay_in_the_title_known_ones_become_assignees() -> None:
    (msg,) = parse(
        "review the dashboard export with @nobody and @sara", known_people=frozenset({"@sara"})
    ).messages
    assert msg.item and "@nobody" in (msg.item.title or "") and "@sara" not in (msg.item.title or "")
    assert [a["person"] for a in msg.assignees] == ["@sara"]


def test_the_card_always_names_the_target_row() -> None:
    (msg,) = parse("it keeps timing out").messages
    rows = build_preview(msg, PACK)
    assert rows[0].label == "Task" and "WEB-114" in rows[0].value and rows[0].cue == "running now"
    nopack = build_preview(msg, ContextPack({"projects": []}))
    assert nopack[0].value == "(no task running)" and nopack[0].cue == "ask"


def test_project_cue_is_inferred_from_the_matched_task_not_the_failure_copy() -> None:
    (msg,) = parse("blocked WEB-114 waiting for ops").messages
    proj = next(r for r in build_preview(msg, PACK) if r.label == "Project")
    assert proj.value == "WEB" and "manager" not in proj.cue
    (new,) = parse("a stray thought about nothing in particular here").messages
    bare = next(r for r in build_preview(new, ContextPack({"projects": []})) if r.label == "Project")
    assert bare.cue == "will be sorted by your manager"  # only when it really is unresolved


class FakeApi:
    def __init__(self, logs: list[dict[str, Any]]) -> None:
        self.logs, self.calls = logs, 0

    def daily_logs(self, day: str) -> list[dict[str, Any]]:
        self.calls += 1
        return self.logs


class Port:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def notify(self, n: Notification) -> None:
        self.sent.append(n)


def test_end_of_day_nudge_fires_once_after_the_log_time_for_an_unconfirmed_draft(tmp_path: Path) -> None:
    port, log = Port(), NudgeLog(tmp_path / "n.json")
    api = FakeApi([{"status": "draft", "minutes_total": 150}])
    morning = datetime(2026, 10, 23, 5, 0, tzinfo=UTC)  # 08:00 Riyadh
    evening = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)  # 18:00 Riyadh
    assert end_of_day_nudge(port, log, api, morning, "Asia/Riyadh") is False
    assert end_of_day_nudge(port, log, api, evening, "Asia/Riyadh") is True
    assert end_of_day_nudge(port, log, api, evening, "Asia/Riyadh") is False  # once per day
    (n,) = port.sent
    assert n.key == "eod:2026-10-23" and "2.5 h" in n.body and n.actions == ["today --confirm"]


def test_no_nudge_without_a_draft_or_when_confirmed_or_when_the_server_is_down(tmp_path: Path) -> None:
    evening = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
    for logs in ([], [{"status": "confirmed", "minutes_total": 60}]):
        assert (
            end_of_day_nudge(Port(), NudgeLog(tmp_path / "x.json"), FakeApi(logs), evening, "Asia/Riyadh")
            is False
        )

    class Down:
        def daily_logs(self, day: str) -> list[dict[str, Any]]:
            raise ConnectionError

    assert end_of_day_nudge(Port(), NudgeLog(tmp_path / "y.json"), Down(), evening, "Asia/Riyadh") is False


def test_end_of_day_due_boundary() -> None:
    assert end_of_day_due(datetime(2026, 1, 1, 17, 30), "17:30") and not end_of_day_due(
        datetime(2026, 1, 1, 17, 29), "17:30"
    )
