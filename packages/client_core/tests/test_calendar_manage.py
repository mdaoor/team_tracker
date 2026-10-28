"""Per-calendar exclude/include, suppress lists and the calendar list refresh (Phase 15 M7/M8)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from calfx import load
from conftest import NOW, FakeClock
from test_calendar_sync import setup, titles
from tt_client.integrations.sync import calendar_overview
from tt_client.personal.model import Category, Kind, PersonalItem, SharePolicy
from tt_client.personal.rules import Hints
from tt_client.personal.settings import Settings
from tt_client.personal.tray import ready_to_send


def test_overview_lists_the_cached_calendars_with_their_state() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    ov = calendar_overview(store)
    google = {c["id"]: c for c in ov["calendars"] if c["provider"] == "google"}
    assert set(google) == {
        "mahmoud@acme.com",
        "team-web@group.calendar.google.com",
        "holidays@group.v.calendar.google.com",
    }
    assert google["mahmoud@acme.com"]["excluded"] is False and google["mahmoud@acme.com"]["primary"] is True
    assert google["holidays@group.v.calendar.google.com"]["selected"] is False
    assert ov["suppress"] == {"attendees": [], "domains": []}


def test_exclude_stops_reading_a_calendar_and_drops_its_unsent_meetings_include_brings_them_back() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    assert titles(store, "google") == ["Client sync with Globex", "Design review", "Retro", "Sprint planning"]
    out = sync.exclude("mahmoud@acme.com")
    assert out["provider"] == "google" and out["dropped"] >= 1
    assert titles(store, "google") == []
    rep = sync.sync("google")
    assert rep.calendars == 1 and titles(store, "google") == []  # not read any more
    flags = {c["id"]: c["excluded"] for c in calendar_overview(store)["calendars"]}
    assert flags["mahmoud@acme.com"] is True and flags["team-web@group.calendar.google.com"] is False
    sync.include("mahmoud@acme.com")
    sync.sync("google")
    assert "Sprint planning" in titles(
        store, "google"
    )  # the sync dropped them, so the sync may bring them back


def test_exclude_accepts_a_name_and_reports_unknown_or_ambiguous_references() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    sync.sync("microsoft")
    assert sync.exclude("web team")["calendar"] == "team-web@group.calendar.google.com"
    assert Settings(store).get("calendar.excluded") == ["team-web@group.calendar.google.com"]
    with pytest.raises(KeyError, match="no calendar"):
        sync.exclude("nope")
    with pytest.raises(KeyError, match="several"):
        sync.exclude("cal")  # 'Calendar' (Outlook) and the ids containing '.calendar.'


def test_excluding_twice_is_idempotent() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    sync.exclude("mahmoud@acme.com")
    sync.exclude("mahmoud@acme.com")
    assert Settings(store).get("calendar.excluded") == ["mahmoud@acme.com"]
    sync.include("mahmoud@acme.com")
    sync.include("mahmoud@acme.com")
    assert Settings(store).get("calendar.excluded") == []


def test_suppress_lists_are_normalised_deduplicated_and_validated() -> None:
    _, store, *_ = setup()
    s = Settings(store)
    assert s.suppress("attendee", " Sara@Acme.com ") == ["sara@acme.com"]
    assert s.suppress("attendee", "sara@acme.com") == ["sara@acme.com"]
    assert s.suppress("domain", "@Globex.com") == ["globex.com"]
    assert s.suppress("attendee", "sara@acme.com", remove=True) == []
    with pytest.raises(ValueError, match="e-mail"):
        s.suppress("attendee", "sara")
    with pytest.raises(ValueError, match="domain"):
        s.suppress("domain", "not a domain")
    assert calendar_overview(store)["suppress"] == {"attendees": [], "domains": ["globex.com"]}


def test_a_suppressed_attendee_or_domain_is_no_longer_offered_in_the_tray() -> None:
    _, store, *_ = setup()
    s = Settings(store)
    start = NOW - timedelta(hours=3)
    store.put_item(
        PersonalItem(
            title="Sync with Globex",
            kind=Kind.MEETING,
            category=Category.MEETING,
            start=start,
            end=start + timedelta(hours=1),
            attendees=["bob@globex.com"],
            share_policy=SharePolicy.ASK,
        )
    )
    assert len(ready_to_send(store, s, Hints(), NOW, "UTC")) == 1
    s.suppress("domain", "globex.com")
    assert ready_to_send(store, s, Hints(), NOW, "UTC") == []
    s.suppress("domain", "globex.com", remove=True)
    s.suppress("attendee", "bob@globex.com")
    assert ready_to_send(store, s, Hints(), NOW, "UTC") == []


def test_refresh_relists_calendars_reports_the_difference_and_drops_a_removed_calendars_meetings() -> None:
    sync, store, gs, _ = setup()
    sync.sync("google")
    listing = load("google/calendar_list.json")
    listing["items"] = [c for c in listing["items"] if not c["id"].startswith("team-web")] + [
        {
            "id": "new@group.calendar.google.com",
            "summary": "Platform",
            "selected": True,
            "accessRole": "reader",
        }
    ]
    gs["calendar_list"] = listing
    out = sync.refresh_calendars("google")
    assert out["error"] is None
    assert out["added"] == ["Platform"] and out["removed"] == ["Web team"]
    ids = {c["id"] for c in calendar_overview(store)["calendars"] if c["provider"] == "google"}
    assert "new@group.calendar.google.com" in ids and "team-web@group.calendar.google.com" not in ids
    assert sync.sync("google").error is None


def test_the_calendar_list_is_refreshed_on_a_schedule_not_cached_forever() -> None:
    clock = FakeClock()
    sync, store, gs, _ = setup(clock)
    sync.sync("google")
    listing = load("google/calendar_list.json")
    listing["items"].append(
        {"id": "late@group.calendar.google.com", "summary": "Late", "selected": True, "accessRole": "reader"}
    )
    gs["calendar_list"] = listing
    clock.advance(hours=Settings(store).get("calendar.refresh_hours") - 1)
    sync.sync("google")
    assert "late@group.calendar.google.com" not in {c["id"] for c in calendar_overview(store)["calendars"]}
    clock.advance(hours=2)
    rep = sync.sync("google")
    assert "late@group.calendar.google.com" in {c["id"] for c in calendar_overview(store)["calendars"]}
    assert rep.calendars == 3  # the new calendar was read in the same sync


def test_a_failing_refresh_keeps_the_old_list_and_reports_the_error() -> None:
    sync, store, gs, _ = setup()
    sync.sync("google")
    gs["valid_token"] = "revoked"
    out = sync.refresh_calendars("google")
    assert out["error"] and "connect" in out["error"]
    assert len([c for c in calendar_overview(store)["calendars"] if c["provider"] == "google"]) == 3


def test_refresh_hours_is_a_setting_and_zero_switches_the_schedule_off() -> None:
    clock = FakeClock()
    sync, store, gs, _ = setup(clock)
    s = Settings(store)
    assert s.get("calendar.refresh_hours") == 24
    s.set("calendar.refresh_hours", 0)
    sync.sync("google")
    listing = load("google/calendar_list.json")
    listing["items"] = listing["items"][:1]
    gs["calendar_list"] = listing
    clock.advance(hours=100)
    sync.sync("google")
    assert len([c for c in calendar_overview(store)["calendars"] if c["provider"] == "google"]) == 3
