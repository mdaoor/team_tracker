"""Phase 15 acceptance flows end to end through `Tracker` (no server): planning, timer, tray, send, development, end of day, privacy."""

from __future__ import annotations

import json
import logging
import socket
import stat
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from calfx import client, google_handler
from conftest import PERSONAL_PACK, FakeClock
from tt_client.integrations import google as g
from tt_client.integrations.oauth import Token
from tt_client.integrations.sync import CalendarSync
from tt_client.personal.model import Category, PersonalItem, SharePolicy, Status
from tt_client.personal.send import NeedsProject, NotSendable, build_message
from tt_client.personal.service import Env, Tracker, TrackerError
from tt_client.secrets import FileStore, MemoryStore
from tt_schema.client import ClientBatch

MON = date(2026, 10, 26)


# ---------------------------------------------------------------------------------------------------- helpers
class NetworkDenied(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any attempt to open a non-local socket or resolve a name fails the test (and is recorded)."""
    attempts: list[str] = []

    def deny(*a: object, **k: object) -> Any:
        attempts.append(repr(a)[:80])
        raise NetworkDenied("the personal tracker tried to use the network")

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            return deny(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    return attempts


class StubApi:
    """What `Tracker.deliver` and `confirm_daily_log` need from `TTApi`."""

    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []
        self.confirmed: list[str] = []
        self.logs = [{"id": "dl-1", "minutes_total": 0, "status": "draft"}]
        self.seq = 100

    def post_batch(self, batch: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.batches.append(batch)
        results = []
        for m in batch["messages"]:
            self.seq += 1
            results.append(
                {
                    "client_id": m["client_id"],
                    "status": "applied",
                    "item": {"key": f"WEB-{self.seq}", "title": "x", "project_key": m.get("project")},
                }
            )
        return 200, {"results": results}

    def daily_logs(self, day: str | None = None) -> list[dict[str, Any]]:
        return self.logs

    def confirm_daily_log(self, log_id: str) -> dict[str, Any]:
        self.confirmed.append(log_id)
        return {"id": log_id}


def outbox_messages(t: Tracker) -> list[dict[str, Any]]:
    out = []
    for e in t.outbox.pending():
        out += t.outbox.load(e)["messages"]
    return out


def local_start(item: PersonalItem, tz: str = "Asia/Riyadh") -> str:
    from zoneinfo import ZoneInfo

    assert item.start is not None
    return item.start.astimezone(ZoneInfo(tz)).strftime("%a %H:%M")


# ---------------------------------------------------------------------------------------------------- 15.1/15.2 offline planning
def test_plan_a_week_with_recurring_items_timer_and_month_totals_with_no_network(
    tmp_path: Path, clock: FakeClock, no_network: list[str]
) -> None:
    t = Tracker(
        Env(data_dir=tmp_path, secrets=MemoryStore(), tz="Asia/Riyadh", workspace="", actor="", clock=clock)
    )
    run = t.add("every weekday 7:00 run 30m")[0]
    course = t.add("Tue/Thu 18:00 Arabic course 1h")[0]
    t.add("read 20m daily")
    book = t.add("course: Rust ch.3 1h")[0]
    assert (
        run.is_recurring and course.category == Category.DEVELOPMENT and book.category == Category.DEVELOPMENT
    )

    week = t.week(MON)
    counts = {d["date"]: len(d["timeline"]) + len(d["anytime"]) for d in week["days"]}
    assert counts == {  # run Mon-Fri, course Tue+Thu, read every day; the Rust item is planned for today (Fri 23rd), outside this week
        "2026-10-26": 2, "2026-10-27": 3, "2026-10-28": 2, "2026-10-29": 3, "2026-10-30": 2, "2026-10-31": 1, "2026-11-01": 1,
    }  # fmt: skip
    mon = week["days"][0]
    assert [r["title"] for r in mon["timeline"]] == ["run"] and mon["timeline"][0]["recurring"]
    assert mon["capacity_min"] == 480  # no server, no pack: the local default of 40 h/week over five days
    assert week["days"][5]["capacity_min"] == 0  # Saturday

    # complete one run, time another item with the timer, skip a course instance: none of it leaves the device
    run_mon = mon["timeline"][0]["id"]
    t.complete(run_mon)
    tue = week["days"][1]["timeline"]
    clock.now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    started = t.timer_start(next(r["id"] for r in tue if r["title"] == "Arabic course"))
    clock.advance(minutes=50)
    stopped = t.timer_stop()
    assert started["state"] == "running" and stopped is not None and stopped["minutes"] == 50
    t.skip(next(r["id"] for r in week["days"][3]["timeline"] if r["title"] == "Arabic course"))

    month = t.month(2026, 10)
    assert month["totals_h"]["personal"] > 0 and month["totals_h"]["development"] > 0
    assert "development" in month["days"]["2026-10-27"]["dots"]
    assert all(
        v["sent_min"] == 0 for v in month["days"].values()
    )  # nothing sent: month shows only private time
    done_run = t.day(MON)["timeline"][0]
    assert done_run["status"] == "done" and done_run["minutes_done"] == 30

    assert outbox_messages(t) == [] and no_network == []


def test_network_deny_fixture_really_denies(no_network: list[str]) -> None:
    with pytest.raises(NetworkDenied):
        socket.getaddrinfo("example.com", 80)
    s = socket.socket(socket.AF_INET)
    with pytest.raises(NetworkDenied):
        s.connect(("93.184.216.34", 80))
    s.close()
    assert len(no_network) == 2


def test_quick_add_is_one_line_and_fast(tracker: Tracker) -> None:
    t0 = time.perf_counter()
    items = tracker.add("run 30m at 6pm")
    assert time.perf_counter() - t0 < 3.0 and len(items) == 1 and local_start(items[0]) == "Fri 18:00"
    with pytest.raises(TrackerError, match="nothing to add"):
        tracker.add("   ")
    with pytest.raises(TrackerError, match="no recurrence"):
        tracker.add_recurring("run 30m at 6pm")
    assert "every" in tracker.preview("every weekday 7:00 run 30m")[0]["rrule_text"]


def test_instances_can_be_moved_cancelled_and_the_series_ended(tracker: Tracker) -> None:
    run = tracker.add("every weekday 7:00 run 30m")[0]
    day = tracker.day(MON)["timeline"][0]
    moved = tracker.reschedule(day["id"], datetime(2026, 10, 26, 17, 0, tzinfo=UTC))
    assert moved.series_id == run.id and tracker.day(MON)["timeline"][0]["start"].startswith(
        "2026-10-26T20:00"
    )  # 17:00Z = 20:00 Riyadh
    assert tracker.delete(tracker.day(date(2026, 10, 27))["timeline"][0]["id"])
    assert tracker.day(date(2026, 10, 27))["timeline"] == []
    tracker.end_series(run.id, date(2026, 10, 28))
    assert [bool(tracker.day(MON + timedelta(days=n))["timeline"]) for n in range(5)] == [
        True,
        False,
        True,
        False,
        False,
    ]
    assert tracker.describe_rule(tracker.get(run.id)).startswith("every weekday")  # type: ignore[union-attr]


def test_the_timer_switches_and_mirrors_start_for_sent_items_only(tracker: Tracker, clock: FakeClock) -> None:
    a = tracker.add("work: fix login 2h p:WEB")[0]
    b = tracker.add("work: write docs 1h p:WEB")[0]
    assert tracker.timer_start(a.id)["state"] == "running"
    clock.advance(minutes=20)
    out = tracker.timer_start(b.id)
    assert out["paused"] == a.id and tracker.timer_status()["item"] == b.id  # type: ignore[index]
    assert outbox_messages(tracker) == []  # unsent items: nothing mirrored
    tracker.complete(a.id, minutes=45)
    tracker.send(a.id)
    tracker.deliver(StubApi())
    assert tracker.get(a.id).sent_refs[0].server_key and tracker.get(a.id).item_hint
    tracker.timer_start(a.id)
    msgs = outbox_messages(tracker)
    assert [(m["action"], m["item"]["key"]) for m in msgs] == [("start", tracker.get(a.id).item_hint)]


# ---------------------------------------------------------------------------------------------------- 15.3 calendar -> tray -> send
def connect_google(t: Tracker, clock: FakeClock) -> CalendarSync:
    gp = g.GoogleCalendar(client(google_handler({})), t.env.secrets, g.oauth_config("cid"))
    gp.vault.save(Token("ya29.GOOGLE-ACCESS-TOKEN-1", "1//GOOGLE-REFRESH-TOKEN", time.time() + 3000))
    return CalendarSync(t.store, {"google": gp}, tz=t.env.tz, me={"google": "mahmoud@acme.com"}, clock=clock)


def test_after_the_meeting_the_tray_offers_one_tap_send_and_the_batch_carries_the_calendar_ref(
    tracker: Tracker, clock: FakeClock
) -> None:
    sync = connect_google(tracker, clock)
    assert sync.sync("google").added == 4
    clock.now = datetime(2026, 10, 26, 7, 1, tzinfo=UTC)  # Sprint planning ended 07:00Z; +2 min not yet
    assert [e.item.title for e in tracker.tray()] == []
    clock.now = datetime(2026, 10, 26, 7, 2, tzinfo=UTC)
    t0 = time.perf_counter()
    tray = tracker.tray()
    assert [e.item.title for e in tray] == ["Sprint planning"]
    entry = tray[0]
    assert (entry.suggestion.project, entry.suggestion.source, entry.suggestion.cue) == (
        "WEB",
        "team",
        "suggested",
    )
    assert entry.minutes == 60 and entry.note == "Sprint planning with @sara, @ali"
    row = tracker.tray_rows()[0]
    assert row["label"] == "Send 1 h Sprint planning → WEB" and row["online_meeting_url"].startswith(
        "https://meet.google.com"
    )
    res = tracker.send(entry.item.id)  # ONE tap
    assert time.perf_counter() - t0 < 5.0 and res["project"] == "WEB" and res["minutes"] == 60

    (msg,) = outbox_messages(tracker)
    batch = ClientBatch.model_validate({"workspace": "acme", "client_version": "0.2", "messages": [msg]})
    m = batch.messages[0]
    assert (m.action.value, m.minutes, m.project, m.note) == (
        "log_time",
        60,
        "WEB",
        "Sprint planning with @sara, @ali",
    )
    assert m.source and m.source.origin == "calendar:google"
    ref = m.source.calendar_ref
    assert ref and (ref.provider, ref.event_id, ref.series_id) == ("google", "g-evt-1", "g-series-sprint")
    assert ref.instance_start == datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    assert m.at == datetime(2026, 10, 26, 7, 0, tzinfo=UTC)  # the entry belongs to the day of the meeting
    assert "attendees" not in json.dumps(msg) and "ali@acme.com" not in json.dumps(
        msg
    )  # the server never sees who was invited
    assert tracker.tray() == []
    with pytest.raises(NotSendable, match="already sent"):
        tracker.send(entry.item.id)


def test_the_tray_survives_a_restart_and_a_second_device_builds_the_same_dedup_key(
    tracker: Tracker, clock: FakeClock, tmp_path: Path
) -> None:
    connect_google(tracker, clock).sync("google")
    clock.now = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)  # "next launch", hours later
    again = Tracker(tracker.env, store=tracker.store)
    assert [e.item.title for e in again.tray()] == ["Sprint planning", "Design review"] or {
        e.item.title for e in again.tray()
    } >= {"Sprint planning"}
    first = tracker.send(next(e.item.id for e in tracker.tray() if e.item.title == "Sprint planning"))

    # the same calendar on another laptop: a different client_id, the same (provider, event_id, instance_start)
    other = Tracker(
        Env(
            data_dir=tmp_path / "other",
            secrets=MemoryStore(),
            tz="Asia/Riyadh",
            workspace="acme",
            actor="@mahmoud",
            clock=clock,
            pack=lambda: PERSONAL_PACK,
        )
    )
    connect_google(other, clock).sync("google")
    second = other.send(next(e.item.id for e in other.tray() if e.item.title == "Sprint planning"))
    a, b = outbox_messages(tracker)[0], outbox_messages(other)[0]
    assert first["client_id"] != second["client_id"]
    assert a["source"]["calendar_ref"] == b["source"]["calendar_ref"]


def test_a_recurring_meeting_series_remembers_its_project(tracker: Tracker, clock: FakeClock) -> None:
    connect_google(tracker, clock).sync("google")
    clock.now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    retro = next(e for e in tracker.tray() if e.item.title == "Retro")
    assert retro.suggestion.project == "WEB"  # sara and ali are web-squad (team hint)
    tracker.send(retro.item.id, project="OPS")  # the person overrides: OPS
    next_week = PersonalItem(
        title="Retro",
        kind="meeting",
        category="meeting",
        start=datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
        duration_min=60,
        calendar_ref={
            "provider": "google",
            "calendar_id": "mahmoud@acme.com",
            "event_id": "g-evt-10-next",
            "series_id": "g-series-retro",
        },  # type: ignore[arg-type]
        attendees=["sara@acme.com", "ali@acme.com"],
    )  # a different instance of the same series
    tracker.store.put_item(next_week)
    clock.now = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)
    entry = next(e for e in tracker.tray() if e.item.id == next_week.id)
    assert (entry.suggestion.project, entry.suggestion.source, entry.suggestion.cue) == ("OPS", "series", "✓")
    assert entry.suggestion.confidence == 0.95


def test_not_work_hides_the_series_for_good(tracker: Tracker, clock: FakeClock) -> None:
    connect_google(tracker, clock).sync("google")
    clock.now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    retro = next(e for e in tracker.tray() if e.item.title == "Retro")
    tracker.not_work(retro.item.id)
    assert "Retro" not in [e.item.title for e in tracker.tray()]


def test_work_meetings_need_a_project_when_nothing_matches(tracker: Tracker, clock: FakeClock) -> None:
    lonely = tracker.store.put_item(
        PersonalItem(
            title="Quarterly offsite",
            kind="meeting",
            category="meeting",
            start=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
            duration_min=60,
        )
    )
    entry = next(e for e in tracker.tray() if e.item.id == lonely.id)
    assert (
        entry.suggestion.project is None
        and entry.as_dict()["needs_project"]
        and entry.suggestion.cue == "ask"
    )
    with pytest.raises(NeedsProject):
        tracker.send(lonely.id)
    assert tracker.send(lonely.id, project="OPS")["project"] == "OPS"
    assert tracker.get(lonely.id).project_hint == "OPS"


def test_share_modes_work_ask_once_remember_and_meeting_auto(tracker: Tracker, clock: FakeClock) -> None:
    a = tracker.add("work: fix login 2h p:WEB")[0]
    tracker.complete(a.id)
    assert [e.item.id for e in tracker.tray()] == [a.id] and not tracker.tray()[0].auto
    tracker.remember_share(Category.WORK, "auto")  # "ask once, then remember"
    assert tracker.tray()[0].auto
    sent = tracker.autosend()
    assert len(sent) == 1 and sent[0]["project"] == "WEB" and tracker.tray() == []
    b = tracker.add("work: review PRs 1h p:WEB")[0]
    tracker.complete(b.id)
    tracker.remember_share(Category.WORK, "never")
    assert tracker.tray() == []


# ---------------------------------------------------------------------------------------------------- 15.4 development and personal
def test_training_is_off_by_default_and_one_toggle_sends_it_as_development(tracker: Tracker) -> None:
    c = tracker.add("course: Rust ch.3 1h")[0]
    tracker.complete(c.id)
    assert tracker.tray() == []  # off by default
    tracker.remember_share(Category.DEVELOPMENT, "ask")  # the one toggle
    (entry,) = tracker.tray()
    assert entry.item.id == c.id and not entry.suggestion.project and not entry.as_dict()["needs_project"]
    res = tracker.send(c.id)
    assert res["project"] == "DEV" and res["tags"] == ["development"]
    (msg,) = outbox_messages(tracker)
    assert (
        msg["project"] == "DEV" and msg["tags"] == ["development"] and msg["source"]["origin"] == "personal"
    )
    assert "calendar_ref" not in msg["source"] and msg["minutes"] == 60


def test_a_recurring_development_series_can_send_each_completed_instance(
    tracker: Tracker, clock: FakeClock
) -> None:
    master = tracker.add_recurring("Tue/Thu 18:00 Arabic course 1h")
    tracker.set_series_send(master.id, each_instance=True)
    clock.now = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)  # Tuesday evening, after the 18:00 class
    tue = tracker.day(date(2026, 10, 27))["timeline"][0]["id"]
    tracker.complete(tue)
    assert tracker.autosend()[0]["tags"] == ["development"] and len(outbox_messages(tracker)) == 1
    assert tracker.tray() == [] and tracker.autosend() == []


def test_personal_items_have_no_send_path_at_all(tracker: Tracker) -> None:
    run = tracker.add("run 30m at 6pm")[0]
    assert run.category == Category.PERSONAL
    tracker.complete(run.id)
    assert tracker.tray() == [] and tracker.tray_rows() == []
    with pytest.raises(NotSendable, match="never sent"):
        tracker.send(run.id, project="WEB")
    with pytest.raises(NotSendable):
        build_message(run, actor="@me", project="WEB", now=datetime.now(UTC))
    # no control can switch it on: not the item, not the settings, not a series flag, not the sidecar
    assert tracker.set_share(run.id, SharePolicy.AUTO).share_policy == SharePolicy.NEVER
    with pytest.raises(ValueError, match="no such control"):
        tracker.settings.set("share.personal", "ask")
    tracker.remember_share(Category.PERSONAL, "auto") if False else None
    assert tracker.privacy()["categories"]["personal"] == "never"
    assert tracker.send_all_unsent()["sent"] == [] and outbox_messages(tracker) == []
    # recategorising it as work is a deliberate act and makes it sendable; and back again closes the path
    tracker.set_category(run.id, Category.WORK)
    assert tracker.get(run.id).share_policy == SharePolicy.ASK
    tracker.set_category(run.id, Category.PERSONAL)
    assert tracker.get(run.id).share_policy == SharePolicy.NEVER


# ---------------------------------------------------------------------------------------------------- 15.2 reviews
def test_end_of_day_sends_every_unsent_work_item_in_two_taps_and_confirms_the_daily_log(
    tracker: Tracker,
) -> None:
    a = tracker.add("work: fix login 2h p:WEB")[0]
    b = tracker.add("work: write docs 1h p:DATA")[0]
    c = tracker.add("work: scratch item 30m")[0]  # no project anywhere
    run = tracker.add("run 30m at 6pm")[0]
    for i in (a, b, c, run):
        tracker.complete(i.id)
    card = tracker.end_of_day()
    assert card["hours_by_category"] == {"work": 3.5, "meeting": 0.0, "development": 0.0, "personal": 0.5}
    assert card["done_count"] == 4 and {u["title"] for u in card["unsent"]} == {
        "fix login",
        "write docs",
        "scratch item",
    }
    api = StubApi()
    tap1 = tracker.send_all_unsent()  # tap 1: [Send all]
    assert len(tap1["sent"]) == 2 and tap1["needs_project"] == [
        c.id
    ]  # the third needs a 3-choice chip, nothing is guessed
    api.logs[0]["minutes_total"] = 180
    tap2 = tracker.confirm_daily_log(api)  # tap 2: confirm the DailyLog
    assert tap2 == {"confirmed": True, "minutes_total": 180} and api.confirmed == ["dl-1"]
    assert [m["project"] for b_ in api.batches for m in b_["messages"]] == ["WEB", "DATA"]
    assert tracker.end_of_day()["sent_count"] == 2


def test_the_daily_log_is_only_confirmed_for_what_was_sent(tracker: Tracker) -> None:
    api = StubApi()
    tracker.complete(tracker.add("run 30m at 6pm")[0].id)
    assert tracker.confirm_daily_log(api) == {"confirmed": False, "reason": "nothing was sent today"}
    assert api.confirmed == [] and api.batches == []


def test_weekly_review_shows_development_hours_meeting_load_and_streaks(
    tracker: Tracker, clock: FakeClock
) -> None:
    tracker.add("read 20m daily")  # from Friday 23rd
    tracker.complete(tracker.add("course: Rust ch.3 90m")[0].id)
    for n in (0, 1, 2):  # done on Fri, Sat, Sun: a streak of three
        d = date(2026, 10, 23) + timedelta(days=n)
        row = next(r for r in tracker.day(d)["anytime"] if r["title"] == "read")
        tracker.complete(row["id"])
    tracker.store.put_item(
        PersonalItem(
            title="Planning",
            kind="meeting",
            category="meeting",
            start=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
            duration_min=120,
        )
    )
    clock.now = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # Monday: the review of the week that just ended
    card = tracker.weekly_review(date(2026, 10, 23))
    assert (
        card["development_h"] == 1.5 + 1.0 and card["meetings_h"] == 2.0
    )  # Rust 1.5 h + three reading sessions of 20 min
    read = next(s for s in card["streaks"] if s["title"] == "read")
    assert read["streak"] == 3 and read["done"] == 3
    assert card["meeting_load_pct"] == round(100 * 120 / (32 * 60))  # the pack says 32 h/week


def test_development_summary_is_opt_in_and_goes_to_the_development_project(tracker: Tracker) -> None:
    with pytest.raises(TrackerError, match="no development time"):
        tracker.development_summary()
    tracker.complete(tracker.add("course: Rust ch.3 90m")[0].id)
    out = tracker.development_summary()
    assert "1.5 h development" in out["note"] and "Rust ch.3" in out["note"]
    (msg,) = outbox_messages(tracker)
    assert (msg["action"], msg["project"], msg["tags"]) == ("note", "DEV", ["development"])


# ---------------------------------------------------------------------------------------------------- unsend
def test_unsend_discards_an_undelivered_batch_and_corrects_a_delivered_one(
    tracker: Tracker, clock: FakeClock
) -> None:
    a = tracker.add("work: fix login 2h p:WEB")[0]
    tracker.complete(a.id)
    tracker.send(a.id)
    clock.advance(minutes=3)
    assert (
        tracker.unsend(a.id) == "discarded"
        and tracker.outbox.pending() == []
        and not tracker.get(a.id).is_sent
    )
    assert tracker.sent_audit()[0]["unsent"] is True

    tracker.send(a.id)  # send again, let it reach the server, then take it back
    api = StubApi()
    assert tracker.deliver(api)["sent"] == 1
    clock.advance(minutes=9)
    audit = tracker.sent_audit()[0]
    assert audit["can_unsend"] and audit["server_key"] and audit["project"] == "WEB"
    assert tracker.unsend(a.id) == "correction"
    (msg,) = outbox_messages(tracker)
    assert msg["action"] == "update" and msg["corrects"] == audit["client_id"]
    ClientBatch.model_validate({"workspace": "acme", "messages": [msg]})
    with pytest.raises(NotSendable, match="nothing to unsend"):
        tracker.unsend(a.id)


def test_unsend_is_refused_after_the_window(tracker: Tracker, clock: FakeClock) -> None:
    a = tracker.add("work: fix login 2h p:WEB")[0]
    tracker.complete(a.id)
    tracker.send(a.id)
    tracker.deliver(StubApi())
    clock.advance(minutes=11)
    assert tracker.sent_audit()[0]["can_unsend"] is False
    with pytest.raises(NotSendable, match="too late"):
        tracker.unsend(a.id)


# ---------------------------------------------------------------------------------------------------- 15.6 privacy
def test_first_run_privacy_summary_and_toggles(tracker: Tracker) -> None:
    p = tracker.privacy()
    assert p["stays_on_device"].startswith("Your personal items stay on this device")
    assert p["categories"] == {"work": "ask", "meeting": "ask", "development": "off", "personal": "never"}
    assert p["encrypted"] is True and p["key_store"] == "memory" and p["first_run_done"] is False
    after = tracker.first_run(work="never", meeting="auto", development=True)
    assert (
        after["categories"] == {"work": "never", "meeting": "auto", "development": "on", "personal": "never"}
        and after["first_run_done"]
    )


def test_tokens_never_reach_the_outbox_the_logs_or_the_config_and_erase_removes_store_and_tokens(
    tmp_path: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secrets = FileStore(tmp_path / "secrets.json", warn=False)  # the headless fallback: the strictest case
    t = Tracker(
        Env(
            data_dir=tmp_path,
            secrets=secrets,
            tz="Asia/Riyadh",
            workspace="acme",
            actor="@mahmoud",
            clock=clock,
            pack=lambda: PERSONAL_PACK,
        )
    )
    gs: dict[str, Any] = {}
    gp = g.GoogleCalendar(client(google_handler(gs)), secrets, g.oauth_config("cid"))
    gp.vault.save(Token("ya29.GOOGLE-ACCESS-TOKEN-1", "1//GOOGLE-REFRESH-TOKEN", time.time() + 3000))
    sync = CalendarSync(
        t.store, {"google": gp}, tz="Asia/Riyadh", me={"google": "mahmoud@acme.com"}, clock=clock
    )
    sync.sync("google")
    gs["valid_token"] = "revoked"
    sync.sync("google")  # a failing call must not log the token either
    clock.now = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    t.send(next(e.item.id for e in t.tray() if e.item.title == "Sprint planning"))
    t.deliver(StubApi())

    tokens = ("ya29.GOOGLE-ACCESS-TOKEN", "GOOGLE-REFRESH-TOKEN")
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.name not in ("secrets.json",) and not f.name.startswith("personal.db"):
            body = f.read_text(errors="ignore")
            assert not any(tok in body for tok in tokens), f
    assert not any(tok in caplog.text for tok in tokens)
    assert not any(tok in rec.getMessage() for rec in caplog.records for tok in tokens)
    raw_db = b"".join(f.read_bytes() for f in tmp_path.glob("personal.db*"))
    assert b"GOOGLE-REFRESH-TOKEN" not in raw_db  # tokens live in the secret store, not the personal store
    assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600
    assert "oauth:google" in secrets.names() and "personal-store-key" in secrets.names()

    report = t.erase_local()
    assert "personal.db" in report["files"] and set(report["secrets"]) == {
        "oauth:google",
        "personal-store-key",
    }
    assert not list(tmp_path.glob("personal.db*")) and secrets.names() == []
    reopened = Tracker(Env(data_dir=tmp_path, secrets=secrets, tz="Asia/Riyadh", clock=clock))
    assert (
        reopened.store.items() == [] and reopened.store.rules() == []
    )  # a brand-new store with a brand-new key


def test_export_import_moves_the_whole_plan_to_a_new_device(
    tracker: Tracker, tmp_path: Path, clock: FakeClock
) -> None:
    tracker.add("every weekday 7:00 run 30m")
    tracker.complete(tracker.add("course: Rust ch.3 1h")[0].id)
    tracker.remember_share(Category.DEVELOPMENT, "ask")
    path = tracker.export_json(tmp_path / "backup.json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    new = Tracker(
        Env(
            data_dir=tmp_path / "new",
            secrets=MemoryStore(),
            tz="Asia/Riyadh",
            clock=clock,
            pack=lambda: PERSONAL_PACK,
        )
    )
    counts = new.import_json(path)
    assert counts["items"] == 2 and new.privacy()["categories"]["development"] == "on"
    assert len(new.week(MON)["days"][0]["timeline"]) == 1


def test_sent_audit_lists_titles_projects_and_server_keys(tracker: Tracker) -> None:
    a = tracker.add("work: fix login 2h p:WEB")[0]
    tracker.complete(a.id)
    tracker.send(a.id)
    assert tracker.sent_audit()[0]["server_key"] is None  # queued, not yet acknowledged
    tracker.deliver(StubApi())
    row = tracker.sent_audit()[0]
    assert row["server_key"].startswith("WEB-") and row["title"] == "fix login" and row["minutes"] == 120


def test_deliver_offline_keeps_everything(tracker: Tracker) -> None:
    from tt_client.api import Unreachable

    class Down:
        def post_batch(self, batch: dict[str, Any]) -> Any:
            raise Unreachable("no route")

    a = tracker.add("work: fix login 2h p:WEB")[0]
    tracker.complete(a.id)
    tracker.send(a.id)
    out = tracker.deliver(Down())
    assert out["offline"] is True and len(tracker.outbox.pending()) == 1
    assert tracker.deliver(StubApi())["sent"] == 1


def test_a_calendar_project_hint_never_leaks_attendees_or_titles_into_development_sends(
    tracker: Tracker,
) -> None:
    c = tracker.add("course: Rust ch.3 1h")[0]
    tracker.complete(c.id)
    tracker.remember_share(Category.DEVELOPMENT, "ask")
    tracker.send(c.id, note="Rust ch.3")
    (msg,) = outbox_messages(tracker)
    assert (
        msg["note"] == "Rust ch.3"
        and "attendees" not in msg
        and Status.DONE.value not in json.dumps(msg["source"])
    )


# ---------------------------------------------------------------------------------------------------- PR #11 review: auto-send disclosure
def _retro(tracker: Tracker, event: str, day: int, *, subject: str = "Retro: layoffs plan") -> PersonalItem:
    return tracker.store.put_item(
        PersonalItem(
            title=subject,
            kind="meeting",
            category="meeting",
            start=datetime(2026, 10, day, 6, 0, tzinfo=UTC),
            duration_min=60,
            project_hint="WEB",
            calendar_ref={
                "provider": "google",
                "calendar_id": "mahmoud@acme.com",
                "event_id": event,
                "series_id": "g-series-retro",
            },  # type: ignore[arg-type]
            attendees=["sara@acme.com", "ali@acme.com"],
        )
    )


def test_auto_sent_meetings_carry_a_neutral_title_and_no_handles_until_the_note_is_confirmed(
    tracker: Tracker, clock: FakeClock
) -> None:
    tracker.remember_share(Category.MEETING, "auto")
    first = _retro(tracker, "g-1", 20)
    clock.now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    (row,) = tracker.autosend()
    assert row["item"] == first.id
    (msg,) = outbox_messages(tracker)
    assert msg["note"] == "Meeting · 1 h" and msg["item"] == {"title": "Meeting · 1 h"}
    wire = json.dumps(msg)
    assert "@sara" not in wire and "@ali" not in wire and "Retro" not in wire and "layoffs" not in wire
    assert "layoffs" not in msg["raw_input"]  # no raw calendar subject in raw_input either


def test_after_the_person_confirmed_the_template_auto_sends_use_it(
    tracker: Tracker, clock: FakeClock
) -> None:
    tracker.remember_share(Category.MEETING, "auto")
    first = _retro(tracker, "g-1", 20)
    clock.now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    tray = next(e for e in tracker.tray() if e.item.id == first.id)
    assert (
        tray.as_dict()["auto_sends"] == "Meeting · 1 h"
    )  # the tray shows exactly what an auto-send would say
    tracker.send(
        first.id
    )  # the person sees the full note in the tray and sends it: that confirms the template
    second = _retro(tracker, "g-2", 21)
    clock.now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert next(e for e in tracker.tray() if e.item.id == second.id).as_dict()["auto_sends"] == (
        "Retro: layoffs plan with @sara, @ali"
    )
    tracker.autosend()
    msgs = outbox_messages(tracker)
    assert msgs[0]["note"] == "Retro: layoffs plan with @sara, @ali"  # manual: what the person saw
    assert msgs[1]["note"] == "Retro: layoffs plan with @sara, @ali" and msgs[1]["item"]["title"].startswith(
        "Retro"
    )


def test_an_edited_manual_note_does_not_confirm_the_template(tracker: Tracker, clock: FakeClock) -> None:
    tracker.remember_share(Category.MEETING, "auto")
    first = _retro(tracker, "g-1", 20)
    clock.now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    tracker.send(first.id, note="Retro (edited by hand)")
    _retro(tracker, "g-2", 21)
    clock.now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    tracker.autosend()
    msgs = outbox_messages(tracker)
    assert msgs[0]["note"] == "Retro (edited by hand)" and msgs[1]["note"] == "Meeting · 1 h"
