from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import tt_cel
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tt_cel.runtime import MAX_NODES, MAX_SOURCE

NOW = datetime(2026, 10, 23, 15, 30, tzinfo=UTC)

# The seven CEL examples of object model §8 (the first six are printed there; the seventh is the unclassified-work rule).
SPEC = [
    'placement.exited_at == null && now - placement.entered_at > duration(string(stage.expected_duration_days) + "d")',
    'event.type == "started" && item.estimate_value == null && item.weight > 1.0',
    'item.state.category == "active" && item.evidence_last_at() < now - business_days(3)',
    'event.type == "finished" && item.actual_end > timestamp(item.planned_end)',
    'metrics.minutes_logged(person.id, "today") > 600',
    "stage.wip_limit != null && stage.wip_count() > stage.wip_limit",
    'item.stage.key == "production" && !has(item.classification.product)',
]


def facts(**kw: Any) -> SimpleNamespace:
    base = dict(
        evidence_last={}, evidence_count={}, hours={}, reopens={}, children={}, violations={}, stage_days={}, missing_dims={}, wip={},
        minutes={}, distinct={}, active={}, overrun={}, now=NOW,
    )  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def root(**over: Any) -> dict[str, Any]:
    from tt_cel.schema import OBJECTS, ROOTS

    def blank(t: str) -> Any:
        if t in OBJECTS:
            return {k: blank(v) for k, v in OBJECTS[t].items()}
        return [] if t.startswith("list<") else {} if t.startswith("map<") else None

    r = {k: blank(v) for k, v in ROOTS.items() if k != "now"}
    r.update(over)
    return r


@pytest.mark.parametrize("src", SPEC)
def test_the_spec_examples_compile_and_type_check_unchanged(src: str) -> None:
    tt_cel.compile(src)


def test_a_typo_is_rejected_at_save_with_the_field_name() -> None:
    with pytest.raises(tt_cel.CelError) as e:
        tt_cel.compile("item.wieght > 3")
    assert e.value.field == "item.wieght" and "wieght" in e.value.message and e.value.line == 1


@pytest.mark.parametrize(
    ("src", "field"),
    [
        ("item.priority > 3", "item.priority"),
        ("item.title + 1", "item.title"),
        ("nobody.x == 1", "nobody"),
        ('item.tags.exists(t, t == "a") && 5', None),
        ("item.nofunc()", "item.nofunc()"),
        ("item.title.foo", "item.title.foo"),
        ("item.stage.key", None),
        ('stage.wip_count(1) > 0 && item.evidence_count("a", "b") > 0', None),
        ("metrics.nope(1)", "metrics.nope()"),
        ("unknown_fn(1) > 0", "unknown_fn()"),
    ],
)
def test_type_and_name_errors_name_the_offender(src: str, field: str | None) -> None:
    with pytest.raises(tt_cel.CelError) as e:
        tt_cel.compile(src)
    if field:
        assert e.value.field == field


def test_syntax_errors_carry_a_position() -> None:
    with pytest.raises(tt_cel.CelError) as e:
        tt_cel.compile("item.weight > ")
    assert e.value.line == 1 and "syntax" in e.value.message


def test_length_and_size_limits() -> None:
    with pytest.raises(tt_cel.CelError, match="characters"):
        tt_cel.compile("item.weight > 1 && " * 400 + "true")
    big = " && ".join(f"item.weight > {i}" for i in range(MAX_NODES // 3))
    assert len(big) < MAX_SOURCE
    with pytest.raises(tt_cel.CelError, match="complex"):
        tt_cel.compile(big)
    with pytest.raises(tt_cel.CelError, match="empty"):
        tt_cel.compile("  ")


def test_evaluation_with_registered_functions() -> None:
    c = tt_cel.compile(
        'item.state.category == "active" && item.evidence_count() == 0 && item.days_in_stage() > 3'
    )
    item = {"id": "i1", "state": {"category": "active"}}
    fn = tt_cel.bind(facts(stage_days={"i1": 5}))
    assert c.evaluate(root(item=item), fn, now=NOW) is True
    fn = tt_cel.bind(facts(stage_days={"i1": 2}))
    assert c.evaluate(root(item=item), fn, now=NOW) is False
    fn = tt_cel.bind(facts(stage_days={"i1": 5}, evidence_count={("i1", None): 2}))
    assert c.evaluate(root(item=item), fn, now=NOW) is False


def test_timestamps_durations_and_business_days() -> None:
    c = tt_cel.compile(
        'item.evidence_last_at() < now - business_days(3) && now - item.created_at > duration("36h")'
    )
    item = {"id": "i1", "created_at": NOW - timedelta(days=2)}
    fn = tt_cel.bind(facts(evidence_last={"i1": NOW - timedelta(days=9)}))
    assert c.evaluate(root(item=item), fn, now=NOW) is True
    fn = tt_cel.bind(facts(evidence_last={"i1": NOW - timedelta(days=1)}))
    assert c.evaluate(root(item=item), fn, now=NOW) is False


def test_templates_expand_validate_and_never_inject() -> None:
    t = tt_cel.builtin_templates()["unclassified_in_stage"]
    src = tt_cel.render(t.cel, t.params, {"stage": 'x" || true || "', "dimension": "region"})
    assert src == 'item.stage.key == "x\\" || true || \\"" && !has(item.classification.region)'
    tt_cel.compile(src)
    with pytest.raises(tt_cel.CelError, match="dimension"):
        tt_cel.render(t.cel, t.params, {"dimension": "a.b || true"})
    with pytest.raises(tt_cel.CelError, match="unknown parameter"):
        tt_cel.render(t.cel, t.params, {"colour": "red"})
    stuck = tt_cel.builtin_templates()["stuck_in_stage"]
    with pytest.raises(tt_cel.CelError, match="between"):
        tt_cel.render(stuck.cel, stuck.params, {"days": 0})
    assert "item.days_in_stage() > 5" in tt_cel.render(stuck.cel, stuck.params, {"days": 5})
    assert "stage.expected_duration_days" in tt_cel.render(stuck.cel, stuck.params, {})


def test_every_builtin_template_compiles_with_defaults_and_renders_its_detail() -> None:
    assert len(tt_cel.builtin_templates()) >= 10
    for key, t in tt_cel.builtin_templates().items():
        src = tt_cel.render(t.cel, t.params, {})
        compiled = tt_cel.compile(src)
        det = tt_cel.compile_detail(t.detail, t.params, {})
        assert compiled.fact_groups >= set() and det is not None, key


def test_detail_text() -> None:
    t = tt_cel.builtin_templates()["stuck_in_stage"]
    det = tt_cel.compile_detail(t.detail, t.params, {})
    ctx = root(item={"id": "i1", "stage": {"key": "test"}}, stage={"expected_duration_days": 5})
    out = det.render(ctx, tt_cel.bind(facts(stage_days={"i1": 12})), now=NOW)
    assert out == "test for 12d (expected ≤ 5)"
    o = tt_cel.builtin_templates()["overrun"]
    d2 = tt_cel.compile_detail(o.detail, o.params, {})
    item = {
        "actual_end": datetime(2026, 10, 25, tzinfo=UTC),
        "planned_end": datetime(2026, 10, 22, tzinfo=UTC),
    }
    assert d2.render(root(item=item), {}, now=NOW) == "finished 2026-10-25 vs plan 2026-10-22"


def test_schema_functions_all_have_docs() -> None:
    assert all(f.doc for f in tt_cel.FUNCTIONS) and tt_cel.VERSION == 1


# ---- fuzz: whatever text arrives, we either reject it with CelError or evaluate it within bounds -----------------------------------
ATOMS = [
    "item.weight", "item.title", "item.state.category", "item.tags", "item.estimate_value", "item.planned_end", "stage.wip_limit",
    "person.handle", "now", "event.payload", "1", "2.5", '"a"', "true", "null", "[1, 2]", 'duration("1h")', "item.evidence_count()",
    "item.days_in_stage()", 'metrics.minutes_logged(person.id, "today")', "business_days(2)", "item.nope", "size(item.tags)",
]  # fmt: skip
OPS = ["&&", "||", "==", "!=", "<", ">", "+", "-", "*", "/", "in"]


@st.composite
def expressions(draw: Any, depth: int = 0) -> str:
    if depth > 3 or draw(st.integers(0, 3)) == 0:
        return draw(st.sampled_from(ATOMS))
    form = draw(st.integers(0, 4))
    a, b = draw(expressions(depth + 1)), draw(expressions(depth + 1))
    if form == 0:
        return f"({a} {draw(st.sampled_from(OPS))} {b})"
    if form == 1:
        return f"!({a})"
    if form == 2:
        return f"({a} ? {b} : {draw(expressions(depth + 1))})"
    if form == 3:
        return f"[{a}, {b}].exists(x, x == {draw(st.sampled_from(ATOMS))})"
    return f"has(item.{draw(st.sampled_from(['title', 'wat', 'stage']))}) && {a}"


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(expressions())
def test_fuzz_never_escapes_the_error_type_or_the_cost_cap(src: str) -> None:
    import time

    try:
        c = tt_cel.compile(src)
    except tt_cel.CelError:
        return
    assert len(src) <= MAX_SOURCE
    t0 = time.perf_counter()
    try:
        c.evaluate(
            root(item={"id": "i", "weight": 1.0, "title": "t", "tags": ["a"], "state": {"category": "open"}}),
            tt_cel.bind(facts()),
            now=NOW,
        )
    except tt_cel.CelError:
        pass
    assert time.perf_counter() - t0 < 0.5


@settings(max_examples=150, deadline=None)
@given(st.text(max_size=120))
def test_fuzz_arbitrary_text_is_a_celerror_or_valid(src: str) -> None:
    try:
        tt_cel.compile(src)
    except tt_cel.CelError:
        pass


def test_comparing_a_value_with_null_is_false_not_an_error() -> None:
    c = tt_cel.compile("stage.wip_limit != null && stage.wip_count() > stage.wip_limit")
    stage = {"wip_limit": 4, "_wip_key": "GOV|build"}
    assert c.evaluate(root(stage=stage), tt_cel.bind(facts(wip={"GOV|build": 6})), now=NOW) is True
    assert c.evaluate(root(stage={"wip_limit": None}), tt_cel.bind(facts()), now=NOW) is False
    e = tt_cel.compile("item.estimate_value == null")
    assert e.evaluate(root(item={"estimate_value": 3.0}), tt_cel.bind(facts()), now=NOW) is False
    assert e.evaluate(root(item={"estimate_value": None}), tt_cel.bind(facts()), now=NOW) is True


def test_on_event_evaluation_cpu_budget() -> None:
    """Phase 8 budget: p95 < 20 ms per on-event evaluation, IO excluded (compile once, evaluate many; typically far below 1 ms)."""
    import time

    c = tt_cel.compile(
        'item.state.category == "active" && item.estimate_value == null && item.weight > 1 && item.days_in_stage() > 3'
    )
    fn = tt_cel.bind(facts(stage_days={"i1": 9}))
    ctx = root(item={"id": "i1", "state": {"category": "active"}, "estimate_value": None, "weight": 2.0})
    times = []
    for _ in range(2000):
        t0 = time.perf_counter()
        assert c.evaluate(ctx, fn, now=NOW) is True
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p95 = times[int(len(times) * 0.95)]
    print("CEL p95 ms", round(p95, 3))
    assert p95 < 20


def test_matches_rejects_patterns_that_can_backtrack_without_bound() -> None:
    """Review B7: celpy hands the raw user regex to `re`; ours refuses nested quantifiers, long patterns and back-references."""
    import time

    c = tt_cel.compile('item.title.matches("(a+)+$")')
    ctx = root(item={"id": "i1", "title": "a" * 40 + "!"})
    t0 = time.perf_counter()
    with pytest.raises(tt_cel.CelError, match="backtrack"):
        c.evaluate(ctx, tt_cel.bind(facts()), now=NOW)
    assert time.perf_counter() - t0 < 1.0  # refused up front, not after minutes of backtracking
    for bad in (
        'item.title.matches("(.*)*x")',
        'item.title.matches("(a|b+)+")',
        'item.title.matches("(a)\\\\1")',
    ):
        with pytest.raises(tt_cel.CelError):
            tt_cel.compile(bad).evaluate(ctx, tt_cel.bind(facts()), now=NOW)
    with pytest.raises(tt_cel.CelError, match="too long"):
        tt_cel.compile('item.title.matches("' + "a" * 300 + '")').evaluate(ctx, tt_cel.bind(facts()), now=NOW)
    ok = tt_cel.compile('item.title.matches("^fix .*login$")')
    assert (
        ok.evaluate(root(item={"id": "i1", "title": "fix the login"}), tt_cel.bind(facts()), now=NOW) is True
    )
