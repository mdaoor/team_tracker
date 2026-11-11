"""Client parity with the REAL server responses (PR #11 review).

The JSON under `fixtures/server/` is recorded from the server app run in-process (`tools/record_client_fixtures.py`),
so a shape change on the server shows up here instead of as a `TypeError` on a user's laptop.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tt_client.pack import ContextPack
from tt_client.rows import DayDraft, ReviewRow, TeamNowRow

SERVER = Path(__file__).parent / "fixtures" / "server"


def recorded(name: str) -> Any:
    return json.loads((SERVER / name).read_text())


def test_a_real_context_pack_with_a_pending_approval_loads_and_compacts() -> None:
    raw = recorded("context-pack.json")
    pack = ContextPack(raw)
    compact = pack.compact()  # used to raise TypeError: 'int' object is not iterable
    assert isinstance(compact["pending_approvals"], list)
    assert isinstance(pack.pending_approvals, list)
    assert json.dumps(compact)  # the translator prompt is built from this
    listed = raw["pending_approvals"]
    count = raw.get("pending_approvals_count", listed if isinstance(listed, int) else len(listed))
    assert count >= 1
    assert len(pack.pending_approvals) in (0, count)  # 0 only while recorded against a count-only server


def test_the_contract_list_shape_is_kept_whole_for_the_translators_and_tt_approve() -> None:
    item = {
        "id": "7d1c",
        "title": "WEB-12 → staging",
        "project_key": "WEB",
        "requested_by": "@sara",
        "requested_at": "2026-10-23T10:00:00+00:00",
    }
    pack = ContextPack({"pending_approvals": [item], "pending_approvals_count": 1})
    assert pack.pending_approvals == [item]
    assert pack.compact()["pending_approvals"] == [
        {"id": "7d1c", "title": "WEB-12 → staging", "project_key": "WEB"}
    ]


@pytest.mark.parametrize("legacy", [0, 3, None])
def test_an_old_server_count_never_crashes_the_client(legacy: Any) -> None:
    pack = ContextPack({"pending_approvals": legacy})
    assert pack.pending_approvals == [] and pack.compact()["pending_approvals"] == []


def test_a_real_review_queue_row_is_mapped_once_into_a_review_row() -> None:
    (raw,) = recorded("review-queue.json")["data"]
    row = ReviewRow.from_api(raw)
    assert (row.ref, row.item_key, row.item_title, row.reporter) == (
        raw["ref"],
        "INBOX-sara-1",
        "tidy the ingest retry",
        "@sara",
    )
    assert row.choice == {"project": "WEB"} and row.choice_label == "WEB"
    three_minutes_later = datetime.fromisoformat(raw["created_at"]) + timedelta(minutes=3)
    assert (
        row.line(three_minutes_later) == "INBOX-sara-1 · tidy the ingest retry · @sara · 3 min → WEB [Enter]"
    )
    doc = row.to_doc(three_minutes_later)
    assert doc["ref"] == raw["ref"] and doc["choice"] == {"project": "WEB"} and "None" not in doc["line"]


def test_a_review_row_without_a_suggestion_says_so_instead_of_printing_none() -> None:
    row = ReviewRow.from_api(
        {"ref": "review_item:1", "item_key": "WEB-1", "item_title": "x", "kind": "stale"}
    )
    assert (
        row.choice is None
        and "None" not in row.line(datetime(2026, 1, 1))
        and "no suggestion" in row.line(datetime(2026, 1, 1))
    )


def test_real_team_now_rows_show_who_what_and_for_how_long() -> None:
    rows = [TeamNowRow.from_api(a) for a in recorded("activities-team-mine.json")["data"]]
    assert rows and all(r.person.startswith("@") and r.item_key and r.item_title for r in rows)
    first = rows[0]
    later = datetime.fromisoformat(first.started_at) + timedelta(minutes=12)
    assert first.line(later) == f"{first.person} · {first.item_key} {first.item_title} · 12 min"


def test_a_real_daily_log_confirms_in_one_plain_sentence() -> None:
    (raw,) = recorded("daily-logs.json")["data"]
    draft = DayDraft.from_api(raw)
    assert draft.status == "draft" and draft.minutes_total == 45
    assert draft.confirmed_sentence() == "Confirmed your day: 45 min on WEB-136."
    assert "{" not in draft.confirmed_sentence()
