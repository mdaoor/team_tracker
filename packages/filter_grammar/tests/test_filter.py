from __future__ import annotations

from datetime import UTC, date, datetime

import jsonschema
import pytest
from hypothesis import given
from hypothesis import strategies as st
from tt_filter import FILTER_SCHEMA, FilterError, parse, resolve, today_in, walk

# the example of object model §9, verbatim
OM_EXAMPLE = {
    "dims": {"product": "PLATFORM-X", "stage": {"in": ["build", "test"]}, "objective": "Q4-O2"},
    "people": {"assignee_in_team": "squad-b"},
    "people2": {"reports_to": "@mahmoud", "kind": ["line", "project"]},
    "priority": {"gte": "high"},
    "weight": {"gte": 2},
    "due_date": {"between": ["tomorrow", "tomorrow"]},
    "touched": {"since": "-14d"},
    "state": {"category": ["open", "active"]},
    "risk": {"open_score_gte": 12},
    "violations": {"open": True, "category": ["time", "process"]},
}


def test_every_key_of_the_object_model_example_parses_and_validates_against_the_schema() -> None:
    node = parse(OM_EXAMPLE)
    assert {c.kind for c in walk(node)} == set(OM_EXAMPLE)
    jsonschema.validate(OM_EXAMPLE, FILTER_SCHEMA)


@pytest.mark.parametrize(
    "doc",
    [
        {"stage": {"in": ["build"], "age_gt_expected": True, "process": "sw"}},
        {"type": {"in": ["bug", "task"]}},
        {"tags": {"all": ["a"], "any": ["b", "c"], "none": ["d"]}},
        {"text": "login"},
        {"project": {"in": ["WEB"]}},
        {"include_inbox": True},
        {"impact": {"gte": 3, "lte": 5}},
        {
            "people": {
                "assignee": ["@sara", "me"],
                "reporter": "@ali",
                "assignment_role": {"role": "reviewer", "person": "@sara"},
            }
        },
        {"people": {"unassigned": True}},
        {"planned_end": {"before": "today"}, "actual_end": {"is_null": True}, "created": {"after": "-7d"}},
        {"dims": {"region": {"under": "emea"}, "tier": {"not_in": ["free"]}, "owner": {"is_null": True}}},
        {"risk": {"linked": "R7"}},
        {"violations": {"severity_gte": "warning"}},
        {"weight": {"gte": 1, "scheme": "default"}},
        {"or": [{"priority": {"eq": "critical"}}, {"not": {"state": {"category": ["done"]}}}]},
        {},
    ],
)
def test_valid_documents(doc: dict) -> None:
    parse(doc)
    jsonschema.validate(doc, FILTER_SCHEMA)


@pytest.mark.parametrize(
    ("doc", "pointer"),
    [
        ({"colour": 1}, "/colour"),
        ({"priority": {"gte": "urgent"}}, "/priority/gte"),
        ({"priority": {"around": 1}}, "/priority/around"),
        ({"weight": {"gte": "x"}}, "/weight/gte"),
        ({"due_date": {"between": ["today"]}}, "/due_date/between"),
        ({"due_date": {"before": "someday"}}, "/due_date/before"),
        ({"due_date": {"before": "today", "after": "today"}}, "/due_date"),
        ({"state": {"category": ["limbo"]}}, "/state/category"),
        ({"dims": {"x": {"in": []}}}, "/dims/x/in"),
        ({"dims": {"x": {"in": ["a"], "not_in": ["b"]}}}, "/dims/x"),
        ({"tags": {}}, "/tags"),
        ({"people2": {"kind": ["line"]}}, "/people2/reports_to"),
        ({"and": []}, "/and"),
        ({"and": [{"nope": 1}]}, "/and/0/nope"),
        ({"not": 3}, "/not"),
        ({"text": ""}, "/text"),
        ({"violations": {"severity_gte": "meh"}}, "/violations/severity_gte"),
        ({"touched": {"since": "yesterday-ish"}}, "/touched/since"),
        ({"include_inbox": "yes"}, "/include_inbox"),
    ],
)
def test_invalid_documents_name_the_pointer(doc: dict, pointer: str) -> None:
    with pytest.raises(FilterError) as exc:
        parse(doc)
    assert exc.value.pointer == pointer


def test_depth_is_limited_to_four() -> None:
    doc: dict = {"text": "x"}
    for _ in range(3):
        doc = {"not": doc}
    parse(doc)  # depth 4
    with pytest.raises(FilterError, match="at most 4"):
        parse({"not": doc})


def test_relative_date_tokens_resolve_in_the_viewers_zone() -> None:
    fri = date(2026, 10, 23)  # a Friday
    assert resolve("today", fri) == fri and resolve("tomorrow", fri) == date(2026, 10, 24)
    assert resolve("start_of_week", fri) == date(2026, 10, 19) and resolve("end_of_week", fri) == date(
        2026, 10, 25
    )
    assert resolve("start_of_month", fri) == date(2026, 10, 1) and resolve("end_of_month", fri) == date(
        2026, 10, 31
    )
    assert resolve("-14d", fri) == date(2026, 10, 9) and resolve("+1w", fri) == date(2026, 10, 30)
    assert resolve("2026-01-02", fri) == date(2026, 1, 2)
    late_utc = datetime(2026, 10, 23, 22, 30, tzinfo=UTC)
    assert today_in("Asia/Riyadh", late_utc) == date(2026, 10, 24)  # already Saturday there
    assert today_in("UTC", late_utc) == date(2026, 10, 23)
    assert today_in("Not/AZone", late_utc) == date(2026, 10, 23)


_word = st.text("abcdefghij", min_size=1, max_size=6)
_leaf = st.one_of(
    st.builds(lambda v: {"priority": {"gte": v}}, st.sampled_from(["low", "high", "critical"])),
    st.builds(lambda v: {"weight": {"lte": v}}, st.integers(0, 50)),
    st.builds(lambda v: {"tags": {"any": v}}, st.lists(_word, min_size=1, max_size=3)),
    st.builds(
        lambda v: {"state": {"category": v}},
        st.lists(st.sampled_from(["open", "active", "done"]), min_size=1, max_size=2),
    ),
    st.builds(lambda v: {"dims": {v: {"in": [v]}}}, _word),
    st.builds(lambda v: {"text": v}, _word),
    st.builds(lambda v: {"due_date": {"before": v}}, st.sampled_from(["today", "-3d", "+2w", "2026-12-31"])),
)
_filters = st.recursive(
    _leaf,
    lambda ch: st.one_of(
        st.builds(lambda a: {"and": a}, st.lists(ch, min_size=1, max_size=3)),
        st.builds(lambda a: {"or": a}, st.lists(ch, min_size=1, max_size=3)),
        st.builds(lambda a: {"not": a}, ch),
    ),
    max_leaves=8,
)


@given(_filters)
def test_random_valid_filters_parse_or_fail_only_on_depth(doc: dict) -> None:
    try:
        parse(doc)
    except FilterError as exc:
        assert "at most 4" in exc.message


@given(st.text(max_size=40))
def test_arbitrary_strings_never_escape_as_other_exceptions(s: str) -> None:
    for doc in (
        {"text": s},
        {"dims": {s or "k": {"in": [s or "v"]}}},
        {"tags": {"any": [s or "t"]}},
        {s or "k": 1},
    ):
        try:
            parse(doc)
        except FilterError:
            pass
