"""CLI <-> server end to end (Phase 2 acceptance): assisted, manual, offline, air-gapped, feedback."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from tt_cli.main import app
from typer.testing import CliRunner

from tests.e2e.conftest import Live

runner = CliRunner()
NOW = "2026-10-23T12:00:00+00:00"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: Live) -> Path:
    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TT_NOW", NOW)
    monkeypatch.delenv("TT_TOKEN", raising=False)
    res = runner.invoke(
        app,
        [
            "login",
            "--url",
            live.url,
            "--workspace",
            live.slug,
            "--handle",
            live.handle,
            "--token",
            live.token,
            "--time-zone",
            "Asia/Riyadh",
        ],
    )
    assert res.exit_code == 0, res.output
    return tmp_path


def tt(*args: str, input: str | None = None) -> str:
    res = runner.invoke(app, list(args), input=input)
    assert res.exit_code == 0, f"tt {' '.join(args)} -> {res.exit_code}\n{res.output}"
    return res.output


def items(live: Live, **params: str) -> list[dict]:  # type: ignore[type-arg]
    r = httpx.get(f"{live.url}/api/v1/w/{live.slug}/items", headers=live.headers(), params=params)
    return r.json()["data"]  # type: ignore[no-any-return]


def test_first_run_prints_ghost_examples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("TT_DATA_DIR", str(tmp_path / "d"))
    res = runner.invoke(app, ["doing", "something"])
    assert res.exit_code == 2 and "tt login" in res.output and "took 2h" in res.output


def test_assisted_free_text_end_to_end(home: Path, live: Live) -> None:
    out = tt("fix the login redirect loop, took 2h, plan to finish tomorrow 5pm", "-p", "web", "--yes")
    assert "log_time" in out and "2 h logged" in out and "Sat 24 Oct 17:00" in out and "✓" in out
    rows = items(live)
    assert [r["title"] for r in rows] == ["fix the login redirect loop"] and rows[0][
        "planned_end"
    ] == "2026-10-24"
    sent = list((home / "data" / "outbox" / "sent").glob("*.json"))
    assert len(sent) == 1
    doc = json.loads(sent[0].read_text())
    assert doc["messages"][0]["raw_input"].startswith("fix the login") and doc["mode"] == "assisted"
    r = httpx.get(f"{live.url}/api/v1/w/{live.slug}/items/{rows[0]['key']}", headers=live.headers())
    assert r.json()["hours_logged"] == 2.0


def test_mode_a_edit_minutes_then_send_records_feedback(
    home: Path, live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = home / "cfg" / "config.toml"
    cfg_path.write_text(cfg_path.read_text().replace("feedback = false", "feedback = true"))
    out = tt("fix the login redirect loop, took 2h", "-p", "web", "--no-send")
    assert "saved" in out
    outbox = list((home / "data" / "outbox").glob("*.json"))
    assert len(outbox) == 1
    batch_id = json.loads(outbox[0].read_text())["messages"][0]["client_id"]
    editor = home / "ed.py"
    editor.write_text(
        "import json,sys\np=sys.argv[1]\nd=json.load(open(p))\nd['messages'][0]['minutes']=90\njson.dump(d,open(p,'w'))\n"
    )
    import sys

    monkeypatch.setenv("EDITOR", f"{sys.executable} {editor}")
    tt("edit", batch_id[:8])
    out = tt("send", batch_id[:8])
    assert "✓" in out
    key = items(live)[0]["key"]
    detail = httpx.get(f"{live.url}/api/v1/w/{live.slug}/items/{key}", headers=live.headers()).json()
    assert detail["hours_logged"] == 1.5
    assert list((home / "data" / "outbox" / "sent").glob("*.json"))
    r = httpx.get(f"{live.url}/api/v1/w/{live.slug}/activities", headers=live.headers())
    assert r.status_code == 200
    # the correction (120 -> 90) reached the server's feedback table
    from sqlalchemy import create_engine, text

    eng = create_engine(_db_url())
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT field, previous, corrected FROM translator_feedback tf JOIN workspaces w "
                "ON w.id = tf.workspace_id WHERE w.slug = :s"
            ),
            {"s": live.slug},
        ).all()
    eng.dispose()
    assert [(r[0], r[1], r[2]) for r in rows] == [("minutes", 120, 90)]


def _db_url() -> str:
    import os

    return os.environ["TT_TEST_DATABASE_URL"]


def test_send_with_jira_flag_is_deferred(home: Path, live: Live) -> None:
    tt("new", "task: send me to jira", "-p", "web", "--no-send")
    ref = next((home / "data" / "outbox").glob("*.json")).name.split("-", 1)[1][:8]
    out = tt("send", ref, "--jira")
    assert "jira:deferred" in out


def test_offline_queue_then_sync_delivers_exactly_once(
    home: Path, live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = home / "cfg" / "config.toml"
    good = cfg_path.read_text()
    cfg_path.write_text(good.replace(live.url, "http://127.0.0.1:1"))  # nothing listens there
    out = tt("doing the offline thing, took 20 min", "--yes")
    assert "offline" in out and "queued 1" in out
    assert "offline" in tt("outbox")  # the pending entry shows its last error
    assert items(live) == []
    cfg_path.write_text(good)
    assert "✓" in tt("sync")
    assert [i["title"] for i in items(live)] == ["the offline thing"]
    assert "empty" in tt("outbox")
    assert tt("sync").strip() == ""  # nothing left; a second sync is a no-op
    assert len(items(live)) == 1


def test_air_gapped_pack_import_form_and_sync(home: Path, live: Live) -> None:
    pack_file = home / "pack.json"
    pack = httpx.get(f"{live.url}/api/v1/w/{live.slug}/context-pack", headers=live.headers()).json()
    pack_file.write_text(json.dumps(pack))
    tt("pack", "--import", str(pack_file))
    cfg_path = home / "cfg" / "config.toml"
    good = cfg_path.read_text()
    cfg_path.write_text(good.replace(live.url, "http://127.0.0.1:1"))
    answers = home / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "action": "create",
                "item": "manual entry",
                "project": "WEB",
                "item_type": "task",
                "tags": ["x"],
                "minutes": "30m",
            }
        )
    )
    out = tt("form", "--answers", str(answers), "--yes")
    assert "offline" in out
    cfg_path.write_text(good)
    assert "✓" in tt("sync")
    row = items(live)[0]
    assert row["title"] == "manual entry"
    sent = json.loads(next((home / "data" / "outbox" / "sent").glob("*.json")).read_text())
    assert sent["mode"] == "manual"


def test_form_refuses_missing_mandatory_fields(home: Path, live: Live) -> None:
    tt("pack", "--refresh")
    answers = home / "a.json"
    answers.write_text(json.dumps({"action": "start", "item": "x"}))
    res = runner.invoke(app, ["form", "--answers", str(answers), "--yes"])
    assert res.exit_code == 2 and "project" in res.output
    assert items(live) == []


def test_form_from_outbox_file_roundtrip(home: Path, live: Live) -> None:
    tt("pack", "--refresh")
    tt("fix login, took 1h", "-p", "web", "--no-send")
    ref = next((home / "data" / "outbox").glob("*.json")).name.split("-", 1)[1][:8]
    out = tt("form", "--from", ref, "--yes")
    assert "✓" in out
    detail = items(live)[0]
    assert detail["title"] == "fix login"


def test_now_today_and_confirm(home: Path, live: Live) -> None:
    tt("doing the active thing", "-p", "web", "--yes")
    assert "the active thing" in tt("now")
    from sqlalchemy import text

    out = tt("doing another thing, took 1h30m", "-p", "web", "--yes")
    assert "another thing" in out
    # build the draft for the CLI's "today" (2026-10-23 in Riyadh) through the domain function
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from tt_server.domain.capture import dailylog

    async def build() -> int:
        eng = create_async_engine(_db_url())
        try:
            async with eng.connect() as conn:
                ws_id = (
                    await conn.execute(text("SELECT id FROM workspaces WHERE slug = :s"), {"s": live.slug})
                ).scalar_one()
            from datetime import date

            return await dailylog.build_for_workspace(
                async_sessionmaker(eng, expire_on_commit=False), ws_id, date(2026, 10, 23)
            )
        finally:
            await eng.dispose()

    assert asyncio.run(build()) == 1
    today = tt("today")
    assert "draft" in today and "another thing" in today
    assert "confirmed" in tt("today", "--confirm")
    assert "confirmed" in tt("today")


def test_now_is_cached_for_offline(home: Path, live: Live) -> None:
    tt("doing cached work", "-p", "web", "--yes")
    tt("now")
    cfg_path = home / "cfg" / "config.toml"
    cfg_path.write_text(cfg_path.read_text().replace(live.url, "http://127.0.0.1:1"))
    assert "offline, last known" in tt("now")


def test_json_and_dry_run_write_nothing(home: Path, live: Live) -> None:
    out = tt("doing a thing", "--dry-run")
    doc = json.loads(out)
    assert doc["messages"][0]["action"] == "start" and doc["workspace"] == live.slug
    assert not list((home / "data" / "outbox").glob("*.json")) and items(live) == []


def test_unknown_project_goes_to_inbox_and_reports_it(home: Path, live: Live) -> None:
    out = tt("note: something odd @ghost", "-p", "nowhere", "--yes")
    assert "✓" in out
    assert "INBOX-mahmoud" in items(live)[0]["key"]


def test_pack_command_reports_counts(home: Path, live: Live) -> None:
    out = tt("pack", "--refresh")
    assert "@mahmoud@" + live.slug in out and "2 projects" in out
    export = home / "exported.json"
    tt("pack", "--export", str(export))
    assert json.loads(export.read_text())["workspace"] == live.slug


def test_a_verbless_noun_phrase_creates_an_item_even_with_a_timer_running(home: Path, live: Live) -> None:
    tt("doing the retry refactor", "-p", "web", "--yes")  # something is running
    out = tt("refactor the ingestion retry", "--yes")
    assert "Task" in out and "new" in out  # the card names the target row
    titles = {r["title"] for r in items(live)}
    assert "refactor the ingestion retry" in titles and len(titles) == 2  # not a note on the running item


def test_empty_input_asks_the_one_question(home: Path) -> None:
    out = tt("say", "")
    assert "What are you working on?" in out and "took 2h" in out


def test_today_drafts_the_log_after_work_was_logged(home: Path, live: Live) -> None:
    tt("fix login, took 45m", "-p", "web", "--yes")
    out = tt("today")
    assert "0.8h" in out or "0.7h" in out or "0.75" in out
    assert "no daily log yet" not in out
