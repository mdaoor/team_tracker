"""`tt day|week|month|add|timer|cal|tray|recur|item|privacy|sent|erase-local|eod|review` against a temp home, no server (Phase 15)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tt_cli.main import app
from tt_client.config import ClientConfig
from tt_client.pack import PackCache
from typer.testing import CliRunner


def _load(name: str, file: str):  # type: ignore[no-untyped-def]
    """Reuse the client_core test doubles by path (several `conftest.py` files exist in one pytest run)."""
    path = Path(__file__).parents[3] / "packages" / "client_core" / "tests" / file
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_calfx = _load("tt_test_calfx", "calfx.py")
client, google_handler = _calfx.client, _calfx.google_handler
PERSONAL_PACK = _load("tt_test_client_conftest", "conftest.py").PERSONAL_PACK

runner = CliRunner()
NOW = "2026-10-23T12:00:00+00:00"  # Friday 15:00 in Riyadh


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TT_NOW", NOW)
    monkeypatch.setenv("TT_SECRETS", "file")
    monkeypatch.delenv("TT_TOKEN", raising=False)
    cfg = ClientConfig(time_zone="Asia/Riyadh")
    cfg.extra["calendar"] = {"email": "mahmoud@acme.com"}
    cfg.save(tmp_path / "cfg" / "config.toml")
    PackCache(tmp_path / "data" / "pack.json").save(PERSONAL_PACK, None)
    return tmp_path


def tt(*args: str, code: int = 0) -> str:
    res = runner.invoke(app, list(args))
    assert res.exit_code == code, f"tt {' '.join(args)} -> {res.exit_code}\n{res.output}"
    return res.output


def first_ref(out: str) -> str:
    m = re.search(r"\b([0-9a-f]{8}(?:@\d{4}-\d{2}-\d{2})?)\b", out)
    assert m, out
    return m.group(1)


def test_it_works_with_no_login_and_no_server(home: Path) -> None:
    out = tt("add", "every weekday 7:00 run 30m")
    assert "added run" in out and "every weekday at 07:00" in out
    tt("add", "read 20m daily")
    day = tt("day", "2026-10-26")
    assert "07:00-07:30" in day and "run" in day and "read" in day
    week = tt("week", "2026-10-26")
    assert week.count(" run ") == 5 and "Sat 31" in week
    assert (home / "data" / "personal.db").exists() and not list((home / "data" / "outbox").glob("*.json"))


def test_the_capacity_comes_from_the_pack(home: Path) -> None:
    tt("add", "run 30m at 6pm")
    assert "of 6 h 24 (8 %)" in tt("day")  # 32 h/week over five days


def test_preview_does_not_create_anything(home: Path) -> None:
    out = tt("add", "--preview", "Tue/Thu 18:00 Arabic course 1h")
    assert "every Tuesday and Thursday at 18:00" in out
    assert "nothing planned" in tt("day")
    rows = json.loads(tt("add", "--preview", "--json", "course: Rust ch.3 1h"))
    assert rows[0]["category"] == "development" and rows[0]["duration_min"] == 60


def test_quick_add_is_fast(home: Path) -> None:
    t0 = time.perf_counter()
    tt("add", "run 30m at 6pm")
    assert time.perf_counter() - t0 < 3.0


def test_add_rejects_an_empty_line_with_a_hint(home: Path) -> None:
    out = tt("add", "  ", code=1)
    assert "nothing to add" in out


def test_timer_start_stop_and_switching(home: Path) -> None:
    a = first_ref(tt("add", "work: fix login 2h p:WEB"))
    b = first_ref(tt("add", "work: write docs 1h p:WEB"))
    assert "▶ fix login" in tt("timer", "start", a)
    assert "paused the previous timer" in tt("timer", "start", b)
    assert "write docs" in tt("timer", "status")
    assert "■ write docs" in tt("timer", "stop")
    assert tt("timer", "stop").startswith("no timer")
    assert "▶ call dentist" in tt("timer", "start", "--title", "call dentist")


def test_the_tray_sends_in_one_step_and_sent_can_be_unsent(home: Path) -> None:
    ref = first_ref(tt("add", "work: fix login 2h p:WEB"))
    assert tt("tray").strip() == "nothing to send"
    tt("item", "done", ref)
    tray = tt("tray")
    assert "1. Send 2 h fix login → WEB" in tray
    out = tt("tray", "send", "1")
    assert "queued 2 h → WEB" in out and "log in with `tt login`" in out
    assert tt("tray").strip() == "nothing to send"
    sent = tt("sent")
    assert "fix login" in sent and "(queued)" in sent and "can unsend" in sent
    assert ".json" in tt("outbox")
    assert "taken back before it reached the server" in tt("sent", "unsend", "1")
    assert tt("outbox").strip() == "outbox is empty" and "unsent" in tt("sent")


def test_a_personal_item_never_reaches_the_tray_and_cannot_be_forced(home: Path) -> None:
    ref = first_ref(tt("add", "run 30m at 6pm"))
    tt("item", "done", ref)
    assert tt("tray").strip() == "nothing to send"
    assert "never sent" in tt("tray", "send", ref, code=1)
    assert "share never" in tt("item", "share", ref, "auto")
    assert "personal: never (no way to send it)" in tt("privacy")


def test_development_needs_the_one_toggle(home: Path) -> None:
    ref = first_ref(tt("add", "course: Rust ch.3 1h"))
    tt("item", "done", ref)
    assert tt("tray").strip() == "nothing to send"
    tt("privacy", "--first-run", "--development")
    assert "Send 1 h Rust ch.3" in tt("tray")
    assert "#development" in tt("tray", "send", "1")
    msgs = json.loads(next((home / "data" / "outbox").glob("*.json")).read_text())["messages"]
    assert (
        msgs[0]["project"] == "DEV"
        and msgs[0]["tags"] == ["development"]
        and msgs[0]["source"]["origin"] == "personal"
    )


def test_recurring_commands(home: Path) -> None:
    out = tt("recur", "add", "Tue/Thu 18:00 Arabic course 1h", "--send-each")
    assert "every Tuesday and Thursday at 18:00" in out
    listing = tt("recur", "list")
    assert "Arabic course" in listing and "send each" in listing
    ref = first_ref(listing)
    assert "ended" in tt("recur", "end", ref, "--until", "2026-11-01")
    assert "no recurrence found" in tt("recur", "add", "run 30m at 6pm", code=1)
    assert "FREQ=WEEKLY;BYDAY=MO,WE,FR" in tt("recur", "add", "--preview", "mon wed fri 12:30 gym 45m")


def test_month_and_review_and_eod_render(home: Path) -> None:
    ref = first_ref(tt("add", "work: fix login 2h p:WEB"))
    tt("item", "done", ref)
    month = tt("month", "2026-10")
    assert "October 2026" in month and "work 2.0" in month
    assert "development" in tt("review")
    eod = tt("eod")
    assert "work 2.0 h" in eod and "unsent: Send 2 h fix login → WEB" in eod
    assert "queued 1; 0 need a project" in tt("eod", "--send-all")
    assert "not logged in" in tt("eod", "--confirm", code=2)


def test_privacy_first_run_and_json(home: Path) -> None:
    assert "choose once" in tt("privacy")
    p = json.loads(
        tt("privacy", "--first-run", "--work", "auto", "--meeting", "ask", "--no-development", "--json")
    )
    assert p["categories"] == {"work": "auto", "meeting": "ask", "development": "off", "personal": "never"}
    assert "Your personal items stay on this device" in tt("privacy")


def test_export_import_and_erase(home: Path, tmp_path: Path) -> None:
    tt("add", "run 30m at 6pm")
    backup = tmp_path / "backup.json"
    assert "wrote" in tt("export", str(backup))
    assert "--yes" in tt("erase-local", code=1)
    out = tt("erase-local", "--yes")
    assert "personal.db" in out and "personal-store-key" in out
    assert not (home / "data" / "personal.db").exists()
    assert "nothing planned" in tt("day")
    assert "'items': 1" in tt("import", str(backup))
    assert "run" in tt("day")


def test_calendar_commands_need_a_client_id_and_sync_through_providers(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "no client id for google" in tt("cal", "connect", "google", code=2)
    assert "no calendar configured" in tt("cal", "sync", code=2)
    from tt_client.integrations import google as g
    from tt_client.integrations.oauth import Token
    from tt_client.secrets import FileStore

    def fake_build(cfg: dict[str, Any], secrets: Any, **kw: Any) -> dict[str, Any]:
        gp = g.GoogleCalendar(client(google_handler({})), secrets, g.oauth_config("cid"))
        gp.vault.save(Token("ya29.GOOGLE-ACCESS-TOKEN-1", "r", time.time() + 3000))
        return {"google": gp}

    monkeypatch.setattr("tt_client.integrations.factory.build_providers", fake_build)
    out = tt("cal", "sync")
    assert "google: +4 ~0 -0" in out and "hidden" in out
    day = tt("day", "2026-10-26")
    assert "Sprint planning" in day and "join: https://meet.google.com" in day and "[meeting]" in day
    assert "Lunch with Omar" not in tt("week", "2026-10-26")  # declined
    assert "google: last ok" in tt("cal", "status")
    assert "oauth:google" in FileStore(home / "data" / "secrets.json").names()
    assert "meeting(s) removed" in tt("cal", "disconnect", "google")
    assert "oauth:google" not in FileStore(home / "data" / "secrets.json").names()


def test_the_sidecar_command_speaks_json(home: Path) -> None:
    tt("add", "run 30m at 6pm")
    day = json.loads(tt("sidecar", "personal_day", "{}"))
    assert day["date"] == "2026-10-23" and day["timeline"][0]["title"] == "run"
    err = json.loads(tt("sidecar", "personal_send", json.dumps({"id": "nope"})))
    assert err["error"]["code"] == "bad_request"
    assert json.loads(tt("sidecar", "personal_nope"))["error"]["code"] == "unknown_command"


def test_login_keeps_the_calendar_section_of_the_config(home: Path) -> None:
    cfg = ClientConfig.load()
    cfg.workspace, cfg.handle = "acme", "@mahmoud"
    cfg.save()
    again = ClientConfig.load()
    assert again.extra["calendar"] == {"email": "mahmoud@acme.com"} and again.time_zone == "Asia/Riyadh"
