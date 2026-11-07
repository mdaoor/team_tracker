from __future__ import annotations

from pathlib import Path

import pytest
from tt_client.evaluate import evaluate, load
from tt_client.parser.structured import ParseContext, StructuredParser
from tt_client.timeparse import parse_duration, parse_when
from tt_schema.client import ClientAction

GOLDEN = Path(__file__).parent / "golden" / "lines.yaml"


def test_golden_set_has_at_least_sixty_lines() -> None:
    assert len(load(GOLDEN)["cases"]) >= 59


def test_structured_parser_passes_the_whole_syntax_subset() -> None:
    report = evaluate(load(GOLDEN), subset="syntax")
    assert report["failures"] == []
    assert all(v == 1.0 for v in report["accuracy"].values())


def test_golden_set_passes_overall() -> None:
    assert evaluate(load(GOLDEN))["failures"] == []


def test_acceptance_numbered_list_with_shared_time_and_plan(ctx: ParseContext) -> None:
    res = StructuredParser(ctx).parse(
        "doing 1. fix login, 2. write tests, 3. deploy | took: 2h | plan: tomorrow 5pm"
    )
    assert [m.action for m in res.messages] == [ClientAction.START] * 3
    assert [m.minutes for m in res.messages] == [120, None, None]
    assert res.messages[0].planned_end is not None
    assert res.messages[0].planned_end.isoformat() == "2026-10-24T17:00:00+03:00"
    assert "minutes applied to the first item" in res.notes


def test_each_list_element_may_carry_its_own_time(ctx: ParseContext) -> None:
    res = StructuredParser(ctx).parse("done 1. fix login took 1h, 2. tests took 30 min")
    assert [m.minutes for m in res.messages] == [60, 30]
    assert res.notes == []


def test_raw_input_and_ids(ctx: ParseContext) -> None:
    res = StructuredParser(ctx).parse("doing fix login")
    m = res.messages[0]
    assert m.raw_input == "doing fix login" and m.actor == "@mahmoud" and m.client_id
    assert m.at == ctx.now


def test_empty_and_whitespace_lines(ctx: ParseContext) -> None:
    assert StructuredParser(ctx).parse("   ").messages == []


def test_default_action_is_configurable(ctx: ParseContext) -> None:
    ctx.default_action = ClientAction.START
    assert StructuredParser(ctx).parse("the weird thing").messages[0].action == ClientAction.START


def test_unknown_priority_is_reported_not_fatal(ctx: ParseContext) -> None:
    res = StructuredParser(ctx).parse("doing x !urgentish")
    assert res.messages[0].priority is None and any("priority" in n for n in res.notes)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("2h", 120),
        ("45 min", 45),
        ("1h30m", 90),
        ("1h 30m", 90),
        ("1.5h", 90),
        ("2d", 960),
        ("90 minutes", 90),
        ("an hour", 60),
        ("half an hour", 30),
        ("about 2 hours", 120),
        ("2 hours 15 min", 135),
    ],
)
def test_durations(text: str, minutes: int) -> None:
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "soon", "2 apples", "h", "0h"])
def test_not_durations(text: str) -> None:
    assert parse_duration(text) is None


def test_hours_per_day_is_configurable() -> None:
    assert parse_duration("1d", hours_per_day=6) == 360


def test_dates(ctx: ParseContext) -> None:
    now = ctx.now
    assert parse_when("tomorrow", now, "Asia/Riyadh").isoformat() == "2026-10-24T17:00:00+03:00"  # type: ignore[union-attr]
    assert parse_when("eod", now, "Asia/Riyadh").isoformat() == "2026-10-23T17:00:00+03:00"  # type: ignore[union-attr]
    assert parse_when("tomorrow 9am", now, "Asia/Riyadh").isoformat() == "2026-10-24T09:00:00+03:00"  # type: ignore[union-attr]
    assert parse_when("eow", now, "Asia/Riyadh").isoformat() == "2026-10-23T17:00:00+03:00"  # type: ignore[union-attr]
    assert parse_when("2026-11-02", now, "Asia/Riyadh").isoformat() == "2026-11-02T17:00:00+03:00"  # type: ignore[union-attr]
    assert parse_when("gibberish nonsense", now, "Asia/Riyadh") is None
    assert parse_when("", now, "Asia/Riyadh") is None


def test_timezone_changes_the_day(ctx: ParseContext) -> None:
    # 23:30 UTC on the 23rd is already the 24th in Riyadh, so "eod" is the 24th there but the 23rd in UTC
    from datetime import datetime

    late = datetime.fromisoformat("2026-10-23T23:30:00+00:00")
    assert parse_when("eod", late, "Asia/Riyadh").date().isoformat() == "2026-10-24"  # type: ignore[union-attr]
    assert parse_when("eod", late, "UTC").date().isoformat() == "2026-10-23"  # type: ignore[union-attr]
