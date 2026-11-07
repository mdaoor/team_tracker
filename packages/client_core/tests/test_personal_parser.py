"""Quick-add golden set (Phase 15 §15.2): >= 60 lines, deterministic, no model."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from tt_client.parser.personal import parse_personal
from tt_client.parser.structured import ParseContext
from tt_client.personal.model import Category, Draft
from tt_client.personal.recurrence import materialise, validate_rrule

GOLDEN = yaml.safe_load((Path(__file__).parent / "golden" / "personal.yaml").read_text())
CASES = GOLDEN["cases"]
CTX = ParseContext(actor="@me", now=datetime.fromisoformat(GOLDEN["now"]), tz=GOLDEN["tz"])


def _value(d: Draft, field: str) -> Any:
    v = getattr(d, field)
    return v.value if hasattr(v, "value") else v


def test_the_golden_set_has_at_least_sixty_lines() -> None:
    assert len(CASES) >= 60


@pytest.mark.parametrize("case", CASES, ids=[c["line"] or "<empty>" for c in CASES])
def test_golden_line(case: dict[str, Any]) -> None:
    drafts = parse_personal(case["line"], CTX)
    expected = case["expect"]
    assert len(drafts) == len(expected), [d.model_dump(mode="json") for d in drafts]
    for draft, want in zip(drafts, expected, strict=True):
        for field, value in want.items():
            if field == "warnings_contain":
                assert any(value in w for w in draft.warnings), draft.warnings
                continue
            got = _value(draft, field)
            if field in ("start", "end") and isinstance(value, str):
                value = datetime.fromisoformat(
                    value
                )  # instants: the draft is UTC, the golden file speaks Riyadh time
            assert got == value, f"{case['line']!r}: {field}: {got!r} != {value!r}"


def test_every_recurring_line_gives_a_valid_rrule_that_materialises() -> None:
    n = 0
    for case in CASES:
        for d in parse_personal(case["line"], CTX):
            if not d.rrule:
                continue
            n += 1
            validate_rrule(d.rrule, CTX.tz)
            item = d.to_item(CTX.tz)
            assert item.is_recurring and item.start is not None
            week = materialise(item, item.start, item.start.replace(year=item.start.year + 1))
            assert week, case["line"]  # the series produces its first occurrence
            assert week[0].start == item.start
    assert n >= 15


def test_a_drafted_item_defaults_follow_the_category() -> None:
    (d,) = parse_personal("course: Rust ch.3 1h", CTX)
    item = d.to_item("Asia/Riyadh")
    assert item.category == Category.DEVELOPMENT and item.share_policy.value == "never"
    (w,) = parse_personal("work: fix login 2h", CTX)
    assert w.to_item("Asia/Riyadh").share_policy.value == "ask"


def test_quick_add_is_fast_enough_for_the_three_second_budget() -> None:
    lines = [c["line"] for c in CASES]
    t0 = time.perf_counter()
    for line in lines:
        parse_personal(line, CTX)
    per_line = (time.perf_counter() - t0) / len(lines)
    assert (
        per_line < 0.05
    )  # the whole "type a line, see the preview" budget is 3 s; parsing is a rounding error
