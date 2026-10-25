"""`tt-sidecar` as the desktop shell runs it: a real subprocess, JSON in, one JSON document out (ADR-0038)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tt_cli import sidecar
from tt_client.catalogue import CatalogueCache
from tt_client.config import ClientConfig
from tt_client.pack import PackCache

CLIENT_TESTS = Path(__file__).parents[3] / "packages" / "client_core" / "tests"
NOW = "2026-10-23T12:00:00+00:00"
PACK = {
    "context_schema_version": 1,
    "actor": "@mahmoud",
    "workspace": "acme",
    "projects": [{"key": "WEB", "name": "Website", "aliases": ["site"]}],
    "my_open_items": [{"key": "WEB-142", "title": "fix the login redirect loop", "project": "WEB"}],
    "people": [{"handle": "@sara", "name": "Sara"}],
    "tags": ["auth"],
    "priorities": ["low", "high"],
    "manual_mode": {"required_fields": ["action", "item", "project"], "per_project": {}},
    "my_current_activity": {"key": "WEB-142", "title": "fix the login redirect loop", "started_at": NOW},
}


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    cfg_dir, data = tmp_path / "cfg", tmp_path / "data"
    # an unreachable server: every network call must degrade, never hang or print prose
    cfg = ClientConfig(
        workspace="acme", handle="@mahmoud", time_zone="Asia/Riyadh", server_url="http://127.0.0.1:9"
    )
    cfg.save(cfg_dir / "config.toml")
    PackCache(data / "pack.json").save(PACK, None)
    cat = json.loads((CLIENT_TESTS / "fixtures" / "catalogue.json").read_text())
    CatalogueCache(data / "catalogue.json").save(cat, cat.get("etag"))
    (data / "me.json").write_text(json.dumps({"role_modes": ["reporter", "manager"], "roles": []}))
    return {
        **os.environ,
        "TT_CONFIG_DIR": str(cfg_dir),
        "TT_DATA_DIR": str(data),
        "TT_NOW": NOW,
        "TT_SECRETS": "file",
        "TT_TOKEN": "",
    }


def run(env: dict[str, str], *argv: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    exe = Path(sys.executable).parent / "tt-sidecar"
    return subprocess.run(  # noqa: S603
        [str(exe), *argv], env=env, input=stdin, capture_output=True, text=True, timeout=60, check=False
    )


def call(env: dict[str, str], verb: str, args: dict[str, Any] | None = None) -> Any:
    res = run(env, verb, json.dumps(args or {}))
    assert res.returncode == 0, res.stderr
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 1, f"stdout must be exactly one JSON document, got: {res.stdout!r}"
    return json.loads(lines[0])


def test_console_script_exists_and_lists_every_verb(env: dict[str, str]) -> None:
    from tt_client.personal.bridge import COMMANDS
    from tt_client.sidecar_commands import VERBS

    out = json.loads(run(env, "--verbs").stdout)
    assert {"parse", "cmd-state", "cmd-send", "personal_day", "personal_eod_confirm"} <= set(out)
    assert set(VERBS) <= set(out) and set(COMMANDS) <= set(out)


def test_personal_verbs_work_with_no_server(env: dict[str, str]) -> None:
    added = call(env, "personal_add", {"line": "run 30m at 6pm"})
    assert added["items"][0]["title"] == "run"
    day = call(env, "personal_day", {"date": "2026-10-23"})
    assert [r["title"] for r in day["timeline"] + day["anytime"]] == ["run"]
    err = call(env, "personal_eod_confirm", {})  # no token: a structured error, not a crash
    assert err["error"]["code"] == "bad_request" and "not logged in" in err["error"]["message"]


def test_capture_parse_pack_send_queue_discard(env: dict[str, str]) -> None:
    assert call(env, "pack")["projects"][0]["key"] == "WEB"
    parsed = call(env, "parse", {"text": "fix the login redirect loop, took 2h", "llm": False})
    assert parsed["source"] == "structured" and parsed["cards"] and parsed["batch"]["workspace"] == "acme"
    assert parsed["batch"]["messages"][0]["minutes"] == 120
    sent = call(env, "send", {"batch": parsed["batch"], "jira": False})
    assert sent["status"] == "queued"  # offline: written first, kept for later
    rows = call(env, "outbox")
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert call(env, "resend", {"id": rows[0]["id"]})["status"] == "queued"
    assert call(env, "discard", {"id": rows[0]["id"]}) == {"discarded": rows[0]["id"]}
    assert call(env, "outbox") == []
    assert "error" in call(env, "discard", {"id": "nope"})
    assert call(env, "active")["key"] == "WEB-142"


def test_build_manual_enforces_mandatory_fields(env: dict[str, str]) -> None:
    bad = call(env, "build-manual", {"answers": {"action": "log_time", "item": "WEB-142"}})
    assert "project" in bad["missing"]
    answers = {"action": "log_time", "item": "WEB-142", "project": "WEB", "minutes": "1h"}
    ok = call(env, "build-manual", {"answers": answers})
    assert ok["mode"] == "manual" and ok["messages"][0]["minutes"] == 60


def test_cmd_verbs_run_from_the_cached_catalogue(env: dict[str, str]) -> None:
    st = call(env, "cmd-state")
    assert st["role"] == "reporter" and [m["mode"] for m in st["modes"]] == ["reporter", "manager"]
    assert call(env, "cmd-role", {"mode": "admin"})["error"].startswith("role mode")
    assert call(env, "cmd-role", {"mode": "manager"})["role"] == "manager"
    assert call(env, "cmd-state")["role"] == "manager"  # persisted in the config, like `tt role`
    prop = call(env, "cmd-propose", {"text": "merge WEB-101 and WEB-105"})
    assert prop["commands"][0]["op"] == "item.merge"
    gate = call(env, "cmd-send", {"commands": prop["commands"]})
    assert gate["status"] == "needs_confirmation"  # the gate lives in the sidecar, offline too


def test_protocol_errors_are_json_and_stdout_stays_clean(env: dict[str, str]) -> None:
    assert "unknown verb" in call(env, "nope")["error"]
    bad = run(env, "pack", "{not json")
    assert bad.returncode == 2 and "bad arguments" in json.loads(bad.stdout)["error"]
    no_args = run(env)
    assert no_args.returncode == 2 and "usage" in json.loads(no_args.stdout)["error"]


def test_args_can_come_from_stdin(env: dict[str, str]) -> None:
    res = run(env, "personal_day", stdin=json.dumps({"date": "2026-10-23"}))
    assert res.returncode == 0 and json.loads(res.stdout)["date"] == "2026-10-23"


def test_serve_mode_answers_one_line_per_request(env: dict[str, str]) -> None:
    lines = [
        {"verb": "pack", "args": {}},
        {"verb": "personal_add", "args": {"line": "read 20m"}},
        {"verb": "nope"},
        "garbage",
    ]
    stdin = "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n"
    res = run(env, "--serve", stdin=stdin)
    assert res.returncode == 0
    out = [json.loads(line) for line in res.stdout.splitlines()]
    assert len(out) == 4
    assert out[0]["projects"][0]["key"] == "WEB" and out[1]["items"][0]["title"] == "read"
    assert "unknown verb" in out[2]["error"] and "bad request line" in out[3]["error"]


def test_a_crashing_handler_still_answers_json(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_v: str, _a: dict[str, Any]) -> Any:
        print("stray print")
        raise RuntimeError("kaboom")

    out = sidecar._answer("x", {}, boom)
    cap = capsys.readouterr()
    assert out == {"error": "RuntimeError: kaboom"} and "stray print" not in cap.out and "kaboom" in cap.err
