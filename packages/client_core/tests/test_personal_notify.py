"""OS-notification policy for the desktop shell (15 M5 / 14 L7): "meeting ended: send?" and the end-of-day nudge, through the
NotificationPort, plus the calendar tick the shell's background loop calls. The Rust side only displays what these return."""

from __future__ import annotations

import time
from typing import Any

from conftest import FakeClock
from test_personal_bridge import ok, populate
from tt_client.notify import Notification, NotificationPort
from tt_client.personal.bridge import handle
from tt_client.personal.notify import CollectingNotifier, notify_due
from tt_client.personal.service import Tracker


def keys(port: CollectingNotifier) -> list[str]:
    return [n.key.split(":")[0] for n in port.sent]


def test_the_collecting_notifier_is_a_notification_port() -> None:
    port: NotificationPort = CollectingNotifier()
    port.notify(Notification("k", "t", "b"))
    assert isinstance(port, CollectingNotifier) and port.sent[0].key == "k"


def test_a_finished_meeting_notifies_once_with_the_project_and_the_command(
    tracker: Tracker, clock: FakeClock
) -> None:
    ids = populate(tracker, clock)
    port = CollectingNotifier()
    notify_due(tracker, port)
    ready = [n for n in port.sent if n.key.startswith("ready:")]
    meeting = next(n for n in ready if n.key == f"ready:{ids['meeting']}")
    assert meeting.title == "Sprint planning ended"
    assert meeting.body == "Send 1 h to WEB?" and meeting.actions == [f"tray send {ids['meeting'][:8]}"]
    work = next(n for n in ready if n.key == f"ready:{ids['work']}")
    assert work.title == "fix login is done"
    again = CollectingNotifier()
    notify_due(tracker, again)
    assert [n for n in again.sent if n.key.startswith("ready:")] == []  # each fires once


def test_an_entry_without_a_known_project_asks_the_person_to_choose(
    tracker: Tracker, clock: FakeClock
) -> None:
    scratch = ok(tracker, "personal_add", {"line": "work: scratch 20m"})["items"][0]
    ok(tracker, "personal_complete", {"id": scratch["id"]})
    port = CollectingNotifier()
    notify_due(tracker, port)
    assert [n.body for n in port.sent if "scratch" in n.title] == ["Pick a project to send 20 min."]


def test_nothing_is_said_about_personal_items(tracker: Tracker) -> None:
    run = ok(tracker, "personal_add", {"line": "run 30m at 6pm"})["items"][0]
    ok(tracker, "personal_complete", {"id": run["id"]})
    port = CollectingNotifier()
    notify_due(tracker, port)
    assert port.sent == []


def test_the_end_of_day_nudge_comes_once_after_17_30_local_when_there_is_something_to_review(
    tracker: Tracker, clock: FakeClock
) -> None:
    populate(tracker, clock)  # 15:03 in Riyadh
    early = CollectingNotifier()
    notify_due(tracker, early)
    assert "eod" not in keys(early)
    clock.advance(hours=2, minutes=30)  # 17:33
    late = CollectingNotifier()
    notify_due(tracker, late)
    eod = next(n for n in late.sent if n.key == "eod:2026-10-23")
    assert eod.title == "How did today go?"
    assert "ready to send" in eod.body and eod.actions == ["eod"]
    again = CollectingNotifier()
    notify_due(tracker, again)
    assert "eod" not in keys(again)
    clock.advance(days=1)  # Saturday: not a workday in the default week
    weekend = CollectingNotifier()
    notify_due(tracker, weekend)
    assert "eod" not in keys(weekend)


def test_no_nudge_on_an_empty_day(tracker: Tracker, clock: FakeClock) -> None:
    clock.advance(hours=6)
    port = CollectingNotifier()
    notify_due(tracker, port)
    assert port.sent == []


def test_the_nudge_time_is_a_setting_and_can_be_switched_off(tracker: Tracker, clock: FakeClock) -> None:
    populate(tracker, clock)
    ok(tracker, "personal_setting_set", {"key": "notify.eod_time", "value": "15:00"})
    port = CollectingNotifier()
    notify_due(tracker, port)
    assert "eod" in keys(port)
    clock.advance(days=3)  # Monday
    ok(tracker, "personal_setting_set", {"key": "notify.eod_time", "value": ""})
    off = CollectingNotifier()
    notify_due(tracker, off)
    assert "eod" not in keys(off)
    assert (
        handle(tracker, "personal_setting_set", {"key": "notify.eod_time", "value": "25:99"})["error"]["code"]
        == "bad_request"
    )


def test_the_bridge_verb_returns_port_shaped_notifications(tracker: Tracker, clock: FakeClock) -> None:
    populate(tracker, clock)
    out = ok(tracker, "personal_notify")
    assert {n["key"].split(":")[0] for n in out["notifications"]} == {"ready"}
    assert set(out["notifications"][0]) == {"key", "title", "body", "actions"}
    assert ok(tracker, "personal_notify")["notifications"] == []


def test_one_shell_tick_answers_calendar_notifications_and_timer_in_a_single_call(
    tracker: Tracker, clock: FakeClock
) -> None:
    populate(tracker, clock)
    out = ok(tracker, "personal_shell_tick")
    assert set(out) == {"calendar", "notifications", "timer"}
    assert out["calendar"] == {"configured": False, "reports": []}
    assert {n["key"].split(":")[0] for n in out["notifications"]} == {"ready"}
    assert out["timer"]["title"] == "write docs" and out["timer"]["elapsed_min"] == 3
    assert ok(tracker, "personal_shell_tick")["notifications"] == []  # once each


def test_the_calendar_tick_is_quiet_without_a_calendar_and_runs_when_due(
    tracker: Tracker, clock: FakeClock, monkeypatch: Any
) -> None:
    assert ok(tracker, "personal_cal_tick") == {"configured": False, "reports": []}
    from calfx import client, google_handler
    from tt_client.integrations import google as g
    from tt_client.integrations.oauth import Token
    from tt_client.secrets import MemoryStore

    secrets = MemoryStore()
    gp = g.GoogleCalendar(client(google_handler({})), secrets, g.oauth_config("cid"))
    gp.vault.save(Token("ya29.GOOGLE-ACCESS-TOKEN-1", "r", time.time() + 3000))
    monkeypatch.setattr("tt_client.personal.bridge._cal_providers", lambda t: {"google": gp})
    first = ok(tracker, "personal_cal_tick")
    assert first["configured"] is True and first["reports"][0]["provider"] == "google"
    assert first["reports"][0]["why"] == "first sync" and first["reports"][0]["added"] == 4
    clock.advance(minutes=2)
    assert ok(tracker, "personal_cal_tick")["reports"] == []  # inside the poll interval
    clock.advance(minutes=4)
    assert ok(tracker, "personal_cal_tick")["reports"][0]["why"] == "poll"
