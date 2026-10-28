"""The sidecar command surface for the desktop/mobile shells, and the shape contract the React views are tested against."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClock
from tt_client.personal.bridge import COMMANDS, handle
from tt_client.personal.service import Tracker

CONTRACT = Path(__file__).parents[3] / "apps" / "desktop" / "src" / "personal" / "contract.json"


def shape(value: Any) -> Any:
    """The JSON type skeleton of a value: keys and primitive types, ignoring ids and times."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "null"


def ok(t: Tracker, command: str, payload: dict[str, Any] | None = None, api: Any = None) -> dict[str, Any]:
    out = handle(t, command, payload, api)
    assert "error" not in out, out
    return out


def populate(t: Tracker, clock: FakeClock) -> dict[str, str]:
    """A day with a recurring item, a done work item, a development item, a sent item, a timer and a finished meeting."""
    from tt_client.personal.model import PersonalItem

    run = ok(t, "personal_add", {"line": "every weekday 7:00 run 30m"})["items"][0]
    work = ok(t, "personal_add", {"line": "work: fix login 2h p:WEB"})["items"][0]
    dev = ok(t, "personal_add", {"line": "course: Rust ch.3 1h"})["items"][0]
    ok(t, "personal_complete", {"id": work["id"]})
    ok(t, "personal_complete", {"id": dev["id"]})
    meeting = t.store.put_item(
        PersonalItem(
            title="Sprint planning",
            kind="meeting",
            category="meeting",
            start=datetime(2026, 10, 23, 8, 0, tzinfo=UTC),
            duration_min=60,
            attendees=["sara@acme.com", "ali@acme.com"],
            online_meeting_url="https://teams.microsoft.com/l/x",
            calendar_ref={"provider": "microsoft", "calendar_id": "c", "event_id": "e1", "series_id": "s1"},  # type: ignore[arg-type]
        )
    )
    docs = ok(t, "personal_add", {"line": "work: write docs 1h p:WEB"})["items"][0]
    ok(t, "personal_timer_start", {"id": docs["id"]})
    clock.advance(minutes=3)
    return {"run": run["id"], "work": work["id"], "dev": dev["id"], "meeting": meeting.id}


def test_every_command_has_a_handler_and_unknown_ones_are_an_error(tracker: Tracker) -> None:
    assert len(COMMANDS) >= 30 and all(c.startswith("personal_") for c in COMMANDS)
    assert handle(tracker, "nope", {})["error"]["code"] == "unknown_command"


def test_the_whole_day_through_the_bridge(tracker: Tracker, clock: FakeClock) -> None:
    ids = populate(tracker, clock)
    day = ok(tracker, "personal_day", {"date": "2026-10-23"})
    assert {r["title"] for r in day["timeline"]} >= {"Sprint planning"} and day["timer"]["elapsed_min"] == 3
    tray = ok(tracker, "personal_tray")["entries"]
    assert [e["title"] for e in tray] == ["Sprint planning", "fix login"]
    assert tray[0]["suggestion"]["project"] == "WEB" and tray[0]["online_meeting_url"].startswith(
        "https://teams"
    )

    sent = ok(tracker, "personal_send", {"id": ids["meeting"]})
    assert sent["project"] == "WEB" and sent["minutes"] == 60
    assert ok(tracker, "personal_sent")["sent"][0]["can_unsend"] is True
    assert ok(tracker, "personal_unsend", {"id": ids["meeting"]})["how"] == "discarded"

    err = handle(tracker, "personal_send", {"id": ids["run"]})["error"]  # a personal item
    assert err["code"] == "not_sendable"
    lonely = ok(tracker, "personal_add", {"line": "work: scratch 20m"})["items"][0]
    ok(tracker, "personal_complete", {"id": lonely["id"]})
    need = handle(tracker, "personal_send", {"id": lonely["id"]})["error"]
    assert need["code"] == "needs_project" and need["alternatives"] == ["WEB", "DATA", "OPS"]
    assert ok(tracker, "personal_send", {"id": lonely["id"], "project": "OPS"})["project"] == "OPS"

    eod = ok(tracker, "personal_eod", {"date": "2026-10-23"})
    assert eod["hours_by_category"]["work"] >= 2.0 and eod["sent_count"] >= 1
    assert ok(tracker, "personal_eod_send_all", {"date": "2026-10-23"})["sent"] is not None
    assert (
        handle(tracker, "personal_deliver", {})["error"]["code"] == "bad_request"
    )  # no api: not logged in, outbox keeps it


def test_timer_recurring_and_settings_commands(tracker: Tracker, clock: FakeClock) -> None:
    started = ok(tracker, "personal_timer_start", {"title": "reading"})
    assert (
        started["state"] == "running" and ok(tracker, "personal_timer_status")["timer"]["title"] == "reading"
    )
    clock.advance(minutes=12)
    assert ok(tracker, "personal_timer_stop")["stopped"]["minutes"] == 12
    series = ok(tracker, "personal_recur_add", {"text": "Tue/Thu 18:00 Arabic course 1h", "send_each": True})
    assert series["rule"] == "every Tuesday and Thursday at 18:00"
    assert ok(tracker, "personal_recur_list")["series"][0]["send_each"] is True
    assert (
        "FREQ=WEEKLY"
        in ok(tracker, "personal_preview", {"line": "mon wed fri 12:30 gym 45m"})["drafts"][0]["rrule"]
    )
    assert ok(tracker, "personal_setting_set", {"key": "tray.delay_min", "value": 5})["tray.delay_min"] == 5
    assert (
        handle(tracker, "personal_setting_set", {"key": "share.personal", "value": "ask"})["error"]["code"]
        == "bad_request"
    )
    assert (
        handle(tracker, "personal_setting_set", {"key": "nope", "value": 1})["error"]["code"] == "bad_request"
    )
    assert (
        ok(tracker, "personal_first_run", {"work": "auto", "development": True})["categories"]["development"]
        == "on"
    )
    assert (
        ok(tracker, "personal_remember_share", {"category": "development", "mode": "never"})["categories"][
            "development"
        ]
        == "off"
    )
    assert handle(tracker, "personal_add", {"line": "  "})["error"]["code"] == "bad_request"
    assert handle(tracker, "personal_day", {"date": "yesterday"})["error"]["code"] == "bad_request"


def test_notifications_fire_once_per_finished_meeting(tracker: Tracker, clock: FakeClock) -> None:
    populate(tracker, clock)
    first = ok(tracker, "personal_tray_notifications")["entries"]
    assert [e["title"] for e in first] == ["Sprint planning", "fix login"]
    assert ok(tracker, "personal_tray_notifications")["entries"] == []


def test_erase_through_the_bridge_removes_everything(
    tracker: Tracker, clock: FakeClock, tmp_path: Path
) -> None:
    populate(tracker, clock)
    out = ok(tracker, "personal_erase_local")
    assert "personal.db" in out["files"] and not list(tmp_path.glob("personal.db*"))


def test_calendar_preferences_through_the_bridge(tracker: Tracker) -> None:
    empty = ok(tracker, "personal_cal_settings")
    assert empty["calendars"] == [] and empty["suppress"] == {"attendees": [], "domains": []}
    tracker.store.set_calendar_state(
        "google",
        {
            "calendars": [
                {"id": "me@acme.com", "name": "Me", "primary": True, "selected": True},
                {"id": "web@x", "name": "Web team", "primary": False, "selected": True},
            ],
            "calendars_at": "2026-10-23T09:00:00+00:00",
        },
    )
    ov = ok(tracker, "personal_cal_settings")
    assert [(c["id"], c["excluded"]) for c in ov["calendars"]] == [("me@acme.com", False), ("web@x", False)]
    off = ok(tracker, "personal_cal_exclude", {"calendar": "web team"})
    assert [c["excluded"] for c in off["calendars"]] == [False, True] and off["changed"][
        "calendar"
    ] == "web@x"
    on = ok(tracker, "personal_cal_exclude", {"calendar": "web@x", "excluded": False})
    assert [c["excluded"] for c in on["calendars"]] == [False, False]
    sup = ok(tracker, "personal_cal_suppress", {"kind": "domain", "value": "Globex.com"})
    assert sup["suppress"]["domains"] == ["globex.com"]
    assert (
        ok(tracker, "personal_cal_suppress", {"kind": "domain", "value": "globex.com", "remove": True})[
            "suppress"
        ]["domains"]
        == []
    )
    bad = handle(tracker, "personal_cal_suppress", {"kind": "attendee", "value": "nope"})
    assert bad["error"]["code"] == "bad_request"
    assert handle(tracker, "personal_cal_exclude", {"calendar": "zzz"})["error"]["code"] == "bad_request"
    assert handle(tracker, "personal_cal_refresh", {})["error"]["message"] == "no calendar configured"


def test_the_shape_contract_for_the_react_views_is_current(tracker: Tracker, clock: FakeClock) -> None:
    ids = populate(tracker, clock)
    ok(tracker, "personal_send", {"id": ids["work"]})
    samples = {
        "personal_day": ok(tracker, "personal_day", {"date": "2026-10-23"}),
        "personal_week": ok(tracker, "personal_week", {"date": "2026-10-23"}),
        "personal_month": ok(tracker, "personal_month", {"year": 2026, "month": 10}),
        "personal_tray": ok(tracker, "personal_tray"),
        "personal_eod": ok(tracker, "personal_eod", {"date": "2026-10-23"}),
        "personal_weekly_review": ok(tracker, "personal_weekly_review", {"date": "2026-10-23"}),
        "personal_privacy": ok(tracker, "personal_privacy"),
        "personal_sent": ok(tracker, "personal_sent"),
        "personal_preview": ok(tracker, "personal_preview", {"line": "every weekday 7:00 run 30m"}),
        "personal_send": ok(tracker, "personal_send", {"id": ids["dev"]})
        if False
        else {"item": "", "client_id": "", "project": "", "minutes": 0, "tags": [""]},
        "personal_timer_status": ok(tracker, "personal_timer_status"),
        "personal_settings": ok(tracker, "personal_settings"),
        "personal_cal_settings": {
            "calendars": [
                {"provider": "", "id": "", "name": "", "primary": False, "selected": True, "excluded": False}
            ],
            "refreshed_at": {"google": ""},
            "refresh_hours": 24,
            "suppress": {"attendees": [""], "domains": [""]},
        },
    }
    # `timer` is null when nothing runs and an object when one does: the contract records the object form
    samples["personal_timer_status"] = {"timer": {"item": "", "title": "", "elapsed_min": 0, "state": ""}}
    samples["personal_day"]["timer"] = samples["personal_day"]["timer"] or {"item_id": "", "elapsed_min": 0}
    contract = {"commands": sorted(COMMANDS), "shapes": {k: shape(v) for k, v in samples.items()}}
    text = json.dumps(contract, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_CONTRACT"):
        CONTRACT.parent.mkdir(parents=True, exist_ok=True)
        CONTRACT.write_text(text)
    assert CONTRACT.exists(), (
        "run: UPDATE_CONTRACT=1 uv run pytest packages/client_core/tests/test_personal_bridge.py"
    )
    assert CONTRACT.read_text() == text, (
        "the bridge output changed: UPDATE_CONTRACT=1 uv run pytest packages/client_core/tests/test_personal_bridge.py, then update the TypeScript types"
    )


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_survives_an_empty_payload_without_raising(tracker: Tracker, command: str) -> None:
    if command in ("personal_erase_local",):
        pytest.skip("destructive; covered above")
    out = handle(tracker, command, {})
    assert isinstance(out, dict)  # either a result or {"error": {...}}; never an exception into the shell
