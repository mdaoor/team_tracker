"""Review B16: a team's trend compares its total with its own previous total (it was always `stable`)."""

from __future__ import annotations

from decimal import Decimal

from tt_schema.enums import Polarity, TargetType, Trend
from tt_server.governance.habits import Counts, _by_subject, _rate, _trend


def test_team_totals_are_built_for_both_windows_and_the_trend_follows() -> None:
    team_of = {"a": "T", "b": "T"}
    now = _by_subject({"a": Counts(occ=1, opp=10), "b": Counts(occ=1, opp=10)}, team_of)
    before = _by_subject({"a": Counts(occ=4, opp=10), "b": Counts(occ=4, opp=10)}, team_of)
    t = (TargetType.TEAM, "T")
    assert now[t].occ == 2 and now[t].opp == 20 and before[t].occ == 8
    rate, prate = _rate(now[t], False), _rate(before[t], False)
    assert rate == Decimal("0.1") and prate == Decimal("0.4")
    assert _trend(Polarity.NEGATIVE, rate, prate) == Trend.IMPROVING  # fewer occurrences of a bad habit
    assert _trend(Polarity.NEGATIVE, prate, rate) == Trend.WORSENING
    # the person rows are untouched by the aggregation
    assert now[(TargetType.PERSON, "a")].occ == 1 and (TargetType.PERSON, "a") in before
