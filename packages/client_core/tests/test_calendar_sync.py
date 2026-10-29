"""Calendar sync into the store: default filters, upsert by calendar_ref, cursors, errors, the poll scheduler (Phase 15 §15.3)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from calfx import client, google_handler, microsoft_handler
from conftest import NOW, FakeClock
from tt_client.integrations import google as g
from tt_client.integrations import microsoft as ms
from tt_client.integrations.oauth import Token
from tt_client.integrations.sync import CalendarSync, Scheduler, skip_reason, to_item
from tt_client.personal.model import Category, Kind, Status
from tt_client.personal.settings import Settings
from tt_client.personal.store import PersonalStore
from tt_client.secrets import MemoryStore

ME = "mahmoud@acme.com"


def setup(clock: FakeClock | None = None):  # type: ignore[no-untyped-def]
    clock = clock or FakeClock()
    store, secrets = PersonalStore.memory(), MemoryStore()
    gs: dict = {}
    ms_state: dict = {}
    gp = g.GoogleCalendar(client(google_handler(gs)), secrets, g.oauth_config("cid"))
    gp.vault.save(Token("ya29.GOOGLE-ACCESS-TOKEN-1", "r1", time.time() + 3000))
    mp = ms.MicrosoftCalendar(client(microsoft_handler(ms_state)), secrets, ms.oauth_config("mid"))
    mp.vault.save(Token("EwB-MS-ACCESS-TOKEN-1", "r2", time.time() + 3000))
    sync = CalendarSync(
        store,
        {"google": gp, "microsoft": mp},
        tz="Asia/Riyadh",
        me={"google": ME, "microsoft": ME},
        clock=clock,
    )
    return sync, store, gs, ms_state


def titles(store: PersonalStore, provider: str) -> list[str]:
    return sorted(i.title for i in store.items() if i.calendar_ref and i.calendar_ref.provider == provider)


def test_google_pull_hides_declined_all_day_private_focus_ooo_and_cancelled() -> None:
    sync, store, *_ = setup()
    rep = sync.sync("google")
    assert (
        rep.error is None and rep.added == 4 and rep.calendars == 2
    )  # the unselected "Holidays" calendar is not read
    assert titles(store, "google") == ["Client sync with Globex", "Design review", "Retro", "Sprint planning"]
    assert rep.skipped == {
        "declined": 1,
        "all-day": 1,
        "private": 1,
        "focus time": 1,
        "out of office": 1,
        "removed": 1,
    }
    sprint = next(i for i in store.items() if i.title == "Sprint planning")
    assert (sprint.kind, sprint.category, sprint.status) == (Kind.MEETING, Category.MEETING, Status.PLANNED)
    assert sprint.calendar_ref.series_id == "g-series-sprint" and sprint.online_meeting_url.startswith(
        "https://meet.google.com"
    )  # type: ignore[union-attr]
    assert sprint.duration_min == 60 and sprint.attendees == ["sara@acme.com", "ali@acme.com"]


def test_google_incremental_sync_updates_adds_and_removes() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    local = next(i for i in store.items() if i.title == "Sprint planning")
    local.project_hint, local.notes = "WEB", "my note"  # local choices must survive a re-sync
    store.put_item(local)
    rep = sync.sync("google")
    assert (rep.added, rep.updated, rep.removed) == (1, 1, 2)
    assert titles(store, "google") == ["1:1 with Ali", "Client sync with Globex", "Sprint planning"]
    moved = next(i for i in store.items() if i.title == "Sprint planning")
    assert moved.id == local.id and moved.start == datetime(2026, 10, 26, 7, 0, tzinfo=UTC)
    assert moved.project_hint == "WEB" and moved.notes == "my note"
    assert sync.status("google")["error"] is None


def test_microsoft_pull_keeps_the_teams_join_link_and_hides_the_rest() -> None:
    sync, store, *_ = setup()
    rep = sync.sync("microsoft")
    assert rep.added == 3 and titles(store, "microsoft") == [
        "Architecture sync",
        "Customer call",
        "Sprint planning",
    ]
    assert rep.skipped == {
        "declined": 1,
        "all-day": 1,
        "private": 1,
        "out of office": 1,
        "removed": 1,
        "focus time": 1,
    }
    teams = next(i for i in store.items() if i.title == "Sprint planning")
    assert teams.online_meeting_url.startswith("https://teams.microsoft.com/l/meetup-join/")  # type: ignore[union-attr]
    assert teams.kind == Kind.MEETING and teams.calendar_ref.provider == "microsoft"  # type: ignore[union-attr]
    rep2 = sync.sync("microsoft")
    assert (rep2.added, rep2.updated, rep2.removed) == (1, 1, 1)
    assert titles(store, "microsoft") == ["Architecture sync", "Roadmap review", "Sprint planning"]


def test_both_providers_can_be_connected_at_once_without_mixing_items() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    sync.sync("microsoft")
    assert len(titles(store, "google")) == 4 and len(titles(store, "microsoft")) == 3
    keys = {(i.calendar_ref.provider, i.calendar_ref.event_id) for i in store.items() if i.calendar_ref}
    assert len(keys) == 7


def test_changing_a_filter_forces_a_full_pull_that_reveals_hidden_events() -> None:
    sync, store, *_ = setup()
    sync.sync("microsoft")
    Settings(store).set("calendar.skip_private", False)
    rep = sync.sync("microsoft")
    assert "Doctor" in titles(store, "microsoft") and rep.added == 1


def test_a_calendar_can_be_switched_off() -> None:
    sync, store, *_ = setup()
    Settings(store).set("calendar.excluded", ["mahmoud@acme.com"])
    rep = sync.sync("google")
    assert rep.calendars == 1 and titles(store, "google") == []


def test_skip_reason_defaults_and_overrides() -> None:
    sync, store, *_ = setup()
    s = Settings(store)
    ev = g.normalise(
        __import__("calfx").load("google/events_page1.json")["items"][2], "c", ME
    )  # the declined lunch
    assert skip_reason(ev, s) == "declined"
    s.set("calendar.skip_declined", False)
    assert skip_reason(ev, s) is None
    assert to_item(ev, "Asia/Riyadh").kind == Kind.MEETING


def test_a_revoked_token_is_reported_not_raised_and_the_plan_is_untouched() -> None:
    sync, store, gs, _ = setup()
    sync.sync("google")
    before = titles(store, "google")
    gs["valid_token"] = "revoked"
    rep = sync.sync("google")
    assert rep.error and "connect" in rep.error
    assert titles(store, "google") == before
    assert "connect" in sync.status("google")["error"]


def test_rate_limits_are_remembered_and_the_scheduler_waits() -> None:
    clock = FakeClock()
    sync, store, gs, _ = setup(clock)
    sched = Scheduler(sync, clock)
    gs["rate_limit"] = True
    ran = sched.tick()
    assert [r[0] for r in ran] == ["google", "microsoft"] and ran[0][2].retry_after == 120
    clock.advance(minutes=1)
    assert sched.due("google") is None  # still inside Retry-After
    gs["rate_limit"] = False
    clock.advance(minutes=4)
    assert sched.due("google") == "poll"


def test_scheduler_first_poll_wake_and_on_demand() -> None:
    clock = FakeClock()
    sync, store, *_ = setup(clock)
    sched = Scheduler(sync, clock)
    assert sched.due("google") == "first sync"
    assert [(n, why) for n, why, _ in sched.tick()] == [("google", "first sync"), ("microsoft", "first sync")]
    clock.advance(minutes=2)
    assert sched.tick() == []  # nothing is due inside the poll interval
    clock.advance(minutes=3)
    assert [why for _, why, _ in sched.tick()] == ["poll", "poll"]
    clock.advance(hours=9)  # the laptop slept
    assert [why for _, why, _ in sched.tick()] == ["wake", "wake"]
    clock.advance(minutes=1)
    sched.request()
    assert [why for _, why, _ in sched.tick()] == ["on demand", "on demand"]
    assert sched.tick() == []


def test_disconnect_forgets_state_and_unsent_meetings_only() -> None:
    sync, store, *_ = setup()
    sync.sync("google")
    keep = next(i for i in store.items() if i.title == "Retro")
    from tt_client.personal.model import SentRef

    keep.sent_refs.append(SentRef(client_id="c1", at=NOW))
    store.put_item(keep)
    removed = sync.disconnect("google")
    assert removed == 3 and titles(store, "google") == ["Retro"] and store.get_calendar_state("google") == {}


def test_the_poll_interval_is_a_setting() -> None:
    clock = FakeClock()
    sync, store, *_ = setup(clock)
    Settings(store).set("calendar.poll_minutes", 15)
    sched = Scheduler(sync, clock)
    sched.tick()
    clock.advance(minutes=10)
    assert sched.due("google") is None
    clock.advance(minutes=6)
    assert sched.due("google") == "poll"
    assert NOW + timedelta(minutes=16) == clock()


def test_microsoft_delta_token_expiry_410_triggers_a_full_resync_that_keeps_dedup() -> None:
    sync, store, _, ms_state = setup()
    sync.sync("microsoft")
    sync.sync("microsoft")  # incremental: adds ms-10 ("Roadmap review"), drops ms-2
    ms1 = next(i for i in store.items() if i.title == "Sprint planning")
    ms1.project_hint = "WEB"  # a local choice must survive the resync
    store.put_item(ms1)
    ms_state["delta_gone"] = True  # the stored deltaLink is now answered with HTTP 410 syncStateNotFound
    rep = sync.sync("microsoft")
    assert rep.error is None, rep.error
    assert sync.status("microsoft")["error"] is None
    rows = [i for i in store.items(include_deleted=True) if i.calendar_ref]
    keys = [(i.calendar_ref.provider, i.calendar_ref.calendar_id, i.calendar_ref.event_id) for i in rows]  # type: ignore[union-attr]
    assert len(keys) == len(set(keys)), "a full resync must not duplicate meetings"
    kept = next(i for i in store.items() if i.title == "Sprint planning")
    assert kept.id == ms1.id and kept.project_hint == "WEB"
    # the full pull is the truth for the window: ms-10 (only in the stale incremental) is gone, and a new cursor is stored
    assert "Roadmap review" not in titles(store, "microsoft")
    cursor = sync.store.get_calendar_state("microsoft")["cursors"]["AAMkAD-cal-1"]["delta_link"]
    assert cursor.endswith("DELTA-1")
    assert sync.sync("microsoft").error is None  # and the next sync is incremental again


def test_a_meeting_the_person_deleted_is_not_resurrected_by_a_sync() -> None:
    for provider, name in (("google", "Client sync with Globex"), ("microsoft", "Architecture sync")):
        sync, store, *_ = setup()
        sync.sync(provider)
        sync.sync(provider)
        gone = next(i for i in store.items() if i.title == name)
        assert store.delete_item(gone.id)
        for full in (False, True):
            rep = sync.sync(provider, full=full)
            assert rep.error is None
            assert name not in titles(store, provider), (provider, full)
        assert rep.skipped.get("deleted locally", 0) >= 1


def test_a_meeting_dropped_by_the_sync_itself_comes_back_when_it_qualifies_again() -> None:
    sync, store, *_ = setup()
    s = Settings(store)
    s.set("calendar.skip_declined", False)
    sync.sync("google")
    assert "Lunch with Omar" in titles(store, "google")
    s.set(
        "calendar.skip_declined", True
    )  # filter change -> full pull -> the declined lunch is dropped by the sync
    sync.sync("google")
    assert "Lunch with Omar" not in titles(store, "google")
    s.set("calendar.skip_declined", False)
    sync.sync("google")
    assert "Lunch with Omar" in titles(store, "google")  # not a local delete: it returns
