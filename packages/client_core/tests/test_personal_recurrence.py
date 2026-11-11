"""RRULE materialisation across DST and time zones (Phase 15 §15.1), including Hypothesis properties."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tt_client.personal.model import PersonalItem, Status
from tt_client.personal.recurrence import (
    describe,
    materialise,
    normalise_rrule,
    resolve_local,
    split_instance_id,
    validate_rrule,
)

ZONES = [
    "America/New_York",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Riyadh",
    "Asia/Kolkata",
    "Australia/Sydney",
    "Australia/Lord_Howe",  # 30-minute DST shift
    "Pacific/Auckland",
    "America/Sao_Paulo",
    "UTC",
]


def series(tz: str, first: datetime, rrule: str, minutes: int = 30) -> PersonalItem:
    return PersonalItem(
        title="run", kind="sport", category="personal", start=first, tz=tz, rrule=rrule, duration_min=minutes
    )


def local(tz: str, y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return resolve_local(datetime(y, mo, d, h, mi), ZoneInfo(tz))


def test_a_daily_morning_run_stays_at_seven_across_the_us_spring_forward() -> None:
    first = local("America/New_York", 2026, 3, 5, 7)
    item = series("America/New_York", first, "FREQ=DAILY")
    got = materialise(item, first, local("America/New_York", 2026, 3, 13, 0))
    z = ZoneInfo("America/New_York")
    assert [g.start.astimezone(z).strftime("%m-%d %H:%M") for g in got] == [
        f"03-{d:02d} 07:00" for d in range(5, 13)
    ]
    # the clocks changed on 8 March: the instants are 24 h apart before, 23 h apart across it
    gaps = [(b.start - a.start).total_seconds() / 3600 for a, b in zip(got, got[1:], strict=False)]
    assert gaps.count(23.0) == 1 and gaps.count(24.0) == 6


def test_a_wall_time_inside_the_spring_forward_gap_moves_forward_but_still_happens() -> None:
    first = local("America/New_York", 2026, 3, 7, 2, 30)  # 02:30 exists on the 7th
    item = series("America/New_York", first, "FREQ=DAILY")
    got = materialise(item, first, local("America/New_York", 2026, 3, 10, 0))
    z = ZoneInfo("America/New_York")
    assert [g.start.astimezone(z).strftime("%m-%d %H:%M") for g in got] == [
        "03-07 02:30",
        "03-08 03:30",
        "03-09 02:30",
    ]


def test_an_ambiguous_autumn_time_is_the_first_occurrence_and_each_day_has_one_instance() -> None:
    first = local("Europe/Berlin", 2026, 10, 24, 2, 30)
    item = series("Europe/Berlin", first, "FREQ=DAILY")
    got = materialise(item, first, first + timedelta(days=3))
    assert [g.instance_date for g in got] == [date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)]
    assert got[1].start.astimezone(ZoneInfo("Europe/Berlin")).utcoffset() == timedelta(
        hours=2
    )  # fold=0: still summer time


def test_weekly_byday_follows_the_local_calendar_not_utc() -> None:
    first = local("Pacific/Auckland", 2026, 10, 27, 8)  # a Tuesday 08:00 in NZ = Monday evening in UTC
    item = series("Pacific/Auckland", first, "FREQ=WEEKLY;BYDAY=TU,TH")
    got = materialise(item, first, first + timedelta(days=14))
    z = ZoneInfo("Pacific/Auckland")
    assert [g.start.astimezone(z).strftime("%a") for g in got] == ["Tue", "Thu", "Tue", "Thu"]
    assert all(g.start.astimezone(z).hour == 8 for g in got)


def test_overrides_replace_complete_skip_and_cancel_instances() -> None:
    first = local("UTC", 2026, 10, 26, 7)
    item = series("UTC", first, "FREQ=DAILY;COUNT=5")
    d = lambda n: date(2026, 10, 26) + timedelta(days=n)  # noqa: E731
    done = item.model_copy(
        update={"id": "x1", "series_id": item.id, "instance_date": d(0), "rrule": None, "status": Status.DONE}
    )
    skipped = item.model_copy(
        update={
            "id": "x2",
            "series_id": item.id,
            "instance_date": d(1),
            "rrule": None,
            "status": Status.SKIPPED,
        }
    )
    gone = item.model_copy(
        update={
            "id": "x3",
            "series_id": item.id,
            "instance_date": d(2),
            "rrule": None,
            "status": Status.CANCELLED,
        }
    )
    got = materialise(item, first, first + timedelta(days=10), [done, skipped, gone])
    assert [(g.instance_date, g.status.value) for g in got] == [
        (d(0), "done"), (d(1), "skipped"), (d(3), "planned"), (d(4), "planned"),
    ]  # fmt: skip


def test_instance_ids_round_trip() -> None:
    first = local("UTC", 2026, 10, 26, 7)
    inst = materialise(series("UTC", first, "FREQ=DAILY"), first, first + timedelta(days=1))[0]
    assert split_instance_id(inst.id) == (inst.series_id, date(2026, 10, 26))
    assert split_instance_id("not-an-instance") is None
    assert split_instance_id("abc@zzz") is None


def test_rrule_normalisation_and_validation() -> None:
    assert normalise_rrule("RRULE:freq=weekly;until=20261231T200000Z") == "FREQ=WEEKLY;UNTIL=20261231T200000"
    assert normalise_rrule("FREQ=DAILY;UNTIL=20261231") == "FREQ=DAILY;UNTIL=20261231T235959"
    validate_rrule("FREQ=MONTHLY;BYMONTHDAY=1")
    with pytest.raises(ValueError):
        validate_rrule("FREQ=SOMETIMES")


def test_until_is_inclusive_of_its_day() -> None:
    first = local("Asia/Riyadh", 2026, 10, 26, 7)
    item = series("Asia/Riyadh", first, "FREQ=DAILY;UNTIL=20261028T235959")
    assert len(materialise(item, first, first + timedelta(days=30))) == 3


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "every weekday at 07:00"),
        ("FREQ=WEEKLY;BYDAY=TU,TH", "every Tuesday and Thursday at 07:00"),
        ("FREQ=DAILY", "every day at 07:00"),
        ("FREQ=DAILY;INTERVAL=2", "every 2 days at 07:00"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "every 2 weeks on Friday at 07:00"),
        ("FREQ=MONTHLY;COUNT=6", "every month, 6 times at 07:00"),
    ],
)
def test_describe(rule: str, text: str) -> None:
    assert describe(rule, datetime(2026, 1, 1, 7, 0)) == text


# ---------------------------------------------------------------------------------------------------- properties
@st.composite
def series_and_window(draw: st.DrawFn) -> tuple[PersonalItem, datetime, datetime, str]:
    tz = draw(st.sampled_from(ZONES))
    year = draw(st.integers(2024, 2028))
    day = draw(st.dates(min_value=date(year, 1, 1), max_value=date(year, 12, 20)))
    hour, minute = draw(st.integers(0, 23)), draw(st.sampled_from([0, 15, 30, 45]))
    first = resolve_local(datetime(day.year, day.month, day.day, hour, minute), ZoneInfo(tz))
    freq = draw(st.sampled_from(["DAILY", "WEEKLY", "WEEKLY-BYDAY"]))
    if freq == "WEEKLY-BYDAY":
        days = draw(
            st.lists(
                st.sampled_from(["MO", "TU", "WE", "TH", "FR", "SA", "SU"]),
                min_size=1,
                max_size=5,
                unique=True,
            )
        )
        rule = "FREQ=WEEKLY;BYDAY=" + ",".join(days)
    else:
        rule = f"FREQ={freq}" + (f";INTERVAL={draw(st.integers(1, 3))}" if draw(st.booleans()) else "")
    span = draw(st.integers(20, 200))
    return (
        series(tz, first, rule, draw(st.sampled_from([15, 30, 60]))),
        first,
        first + timedelta(days=span),
        tz,
    )


PROP = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@PROP
@given(series_and_window())
def test_instances_are_ordered_unique_and_keep_their_wall_clock(
    case: tuple[PersonalItem, datetime, datetime, str],
) -> None:
    item, lo, hi, tz = case
    z = ZoneInfo(tz)
    got = materialise(item, lo, hi)
    assert got, "a rule with no end always yields instances in a 20+ day window"
    starts = [g.start for g in got]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert len({g.instance_date for g in got}) == len(got)
    want = item.start.astimezone(z)  # type: ignore[union-attr]
    for g in got:
        local_start = g.start.astimezone(z)
        assert local_start.date() == g.instance_date or (local_start.date() - g.instance_date).days == 0
        minutes_wanted = want.hour * 60 + want.minute
        minutes_got = local_start.hour * 60 + local_start.minute
        # the wall clock is kept, except when that wall time does not exist on that day (a spring-forward gap): then it is later, never earlier
        assert (
            minutes_got == minutes_wanted
            or 0 < minutes_got - minutes_wanted <= 60
            or minutes_got < minutes_wanted <= 60
        )
        assert g.end is not None and (g.end - g.start) == timedelta(minutes=item.duration_min or 0)
        assert lo <= g.start < hi


@PROP
@given(series_and_window(), st.integers(1, 15))
def test_windows_partition_cleanly(case: tuple[PersonalItem, datetime, datetime, str], cut_days: int) -> None:
    item, lo, hi, _ = case
    mid = lo + timedelta(days=cut_days)
    whole = [g.id for g in materialise(item, lo, hi)]
    left = [g.id for g in materialise(item, lo, mid)]
    right = [g.id for g in materialise(item, mid, hi)]
    assert left + right == whole  # no instance twice, none lost at the seam


@PROP
@given(series_and_window())
def test_daily_rules_have_one_instance_per_local_day(
    case: tuple[PersonalItem, datetime, datetime, str],
) -> None:
    item, lo, hi, tz = case
    if item.rrule != "FREQ=DAILY":
        return
    z = ZoneInfo(tz)
    got = materialise(item, lo, hi)
    days = [g.instance_date for g in got]
    assert days == [
        days[0] + timedelta(days=n) for n in range(len(days))
    ]  # consecutive local dates, DST or not
    assert all(g.start.astimezone(z).date() >= g.instance_date for g in got)


@PROP
@given(st.sampled_from(ZONES), st.datetimes(min_value=datetime(2024, 1, 1), max_value=datetime(2029, 1, 1)))
def test_resolve_local_is_total_and_monotone_within_a_day(tz: str, naive: datetime) -> None:
    z = ZoneInfo(tz)
    a = resolve_local(naive, z)
    b = resolve_local(naive + timedelta(minutes=1), z)
    assert a.utcoffset() is not None
    assert b >= a  # time never goes backwards, even through a gap
    assert a.astimezone(UTC).tzinfo is UTC
