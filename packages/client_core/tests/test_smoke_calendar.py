"""tools/smoke_calendar.py against fake Google and Graph servers: the table, the skips, the failure paths and the no-credentials exit.

The REAL accounts cannot be reached from here; this proves the script's logic, not the providers. See docs/uat/uat-personal-tracker.md."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
from calfx import google_handler, json_response, load, microsoft_handler

SCRIPT = Path(__file__).parents[3] / "tools" / "smoke_calendar.py"
spec = importlib.util.spec_from_file_location("smoke_calendar", SCRIPT)
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke_calendar"] = smoke
spec.loader.exec_module(smoke)

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
GENV = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_REFRESH_TOKEN": "1//REFRESH-SECRET-G"}
MENV = {"MS_CLIENT_ID": "mid", "MS_REFRESH_TOKEN": "M.R3.REFRESH-SECRET-M"}


def fake_world(
    *, read_only: bool = False, bad_refresh: bool = False, delete_fails: bool = False
) -> tuple[httpx.Client, dict[str, Any]]:
    """A Google + Graph in one: reads from the calfx fixtures, event create/get/delete kept in a dict."""
    ctx: dict[str, Any] = {"events": {}, "requests": [], "n": 0}
    gs: dict[str, Any] = {"valid_token": "ya29.GOOGLE-ACCESS-TOKEN-2"}
    mstate: dict[str, Any] = {"valid_token": "EwB-MS-ACCESS-TOKEN-2"}
    g_read, m_read = google_handler(gs), microsoft_handler(mstate)

    def handler(req: httpx.Request) -> httpx.Response:
        ctx["requests"].append((req.method, str(req.url)))
        url = str(req.url)
        u = urlparse(url)
        is_token = u.path.endswith("/token")
        if is_token:
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if bad_refresh:
                return json_response(
                    {"error": "invalid_grant", "error_description": "Token has been revoked."}, 400
                )
            wants_write = "calendar.events" in form.get("scope", "") or "ReadWrite" in form.get("scope", "")
            if wants_write and read_only:
                return json_response({"error": "invalid_scope", "error_description": "not granted"}, 400)
            if wants_write:
                body = load(
                    "google/token_refreshed.json" if "google" in url else "microsoft/token_refreshed.json"
                )
                return json_response({**body, "access_token": body["access_token"].replace("-2", "-W")})
        authed = req.headers.get("Authorization", "")
        write_tokens = {"Bearer ya29.GOOGLE-ACCESS-TOKEN-W", "Bearer EwB-MS-ACCESS-TOKEN-W"}
        if authed in write_tokens:
            # event endpoints, Google then Graph
            google = "googleapis.com" in url
            ev_re = "/events" if google else "/me/events"
            if u.path.endswith(ev_re) and req.method == "POST":
                ctx["n"] += 1
                eid = f"smoke-event-{ctx['n']}-abcdefghijklmnop"
                body = json.loads(req.content)
                ctx["events"][eid] = body
                return json_response({"id": eid, **body}, 201)
            eid = unquote(u.path.rsplit("/", 1)[-1])
            if eid in ctx["events"] or eid.startswith("smoke-event"):
                if req.method == "GET":
                    if eid not in ctx["events"]:
                        return json_response({"error": {"code": 404}}, 404)
                    return json_response({"id": eid, **ctx["events"][eid]})
                if req.method == "DELETE":
                    if delete_fails:
                        return httpx.Response(500, json={"error": "boom"})
                    ctx["events"].pop(eid, None)
                    return httpx.Response(204)
        return (g_read if "googleapis.com" in url or "oauth2.googleapis" in url else m_read)(req)

    return httpx.Client(transport=httpx.MockTransport(handler)), ctx


def run(env: dict[str, str], argv: list[str] | None = None, **world: bool) -> tuple[int, str, dict[str, Any]]:
    http, ctx = fake_world(**world)
    lines: list[str] = []
    code = smoke.main(argv or [], env=env, http=http, clock=lambda: NOW, out=lines.append)
    return code, "\n".join(lines), ctx


def test_without_credentials_it_skips_cleanly_with_exit_zero() -> None:
    code, out, ctx = run({})
    assert code == 0 and out.startswith("skipped: no credentials") and ctx["requests"] == []
    code2, out2, _ = run({"GOOGLE_CLIENT_ID": "only-half"})
    assert code2 == 0 and out2.startswith("skipped: no credentials")


def results(out: str, provider: str) -> list[tuple[str, str]]:
    names = [
        "OAuth refresh", "list calendars", "read one window", "incremental read", "refresh with write scope",
        "create test event", "read it back", "delete it", "confirm it is gone",
    ]  # fmt: skip
    found = []
    for line in out.splitlines():
        if line.startswith(provider):
            for n in names:
                if f" {n} " in f" {line} ":
                    status = next(w for w in line.split() if w in ("PASS", "FAIL", "SKIP"))
                    found.append((n, status))
    return found


def test_a_full_google_run_passes_every_step_and_deletes_the_test_event() -> None:
    code, out, ctx = run(GENV)
    assert code == 0, out
    assert [s for _, s in results(out, "google")] == ["PASS"] * 9
    assert ctx["events"] == {}  # created, then deleted
    methods = [m for m, _ in ctx["requests"]]
    assert methods.count("POST") >= 2 and "DELETE" in methods  # the token POSTs and the event POST
    assert "REFRESH-SECRET" not in out and "ACCESS-TOKEN" not in out  # no secret ever reaches the table
    assert "9 passed, 1 skipped, 0 failed" in out  # the 1 skip is the Microsoft "not set" row


def test_a_full_microsoft_run_passes_every_step() -> None:
    code, out, ctx = run(MENV)
    assert code == 0, out
    assert [s for _, s in results(out, "microsoft")] == ["PASS"] * 9
    assert ctx["events"] == {} and "REFRESH-SECRET" not in out


def test_both_providers_in_one_table() -> None:
    code, out, _ = run({**GENV, **MENV})
    assert code == 0 and "18 passed, 0 skipped, 0 failed" in out
    assert out.splitlines()[0].split()[:3] == ["provider", "check", "result"]


def test_a_read_only_token_skips_the_write_steps_instead_of_failing() -> None:
    code, out, ctx = run(GENV, read_only=True)
    assert code == 0, out
    statuses = dict(results(out, "google"))
    assert statuses["OAuth refresh"] == statuses["read one window"] == "PASS"
    assert statuses["refresh with write scope"] == statuses["create test event"] == "SKIP"
    assert "--connect google" in out and ctx["events"] == {}


def test_a_failed_delete_is_a_failure_that_names_the_event_to_clean_up() -> None:
    code, out, ctx = run(GENV, delete_fails=True)
    assert code == 1
    assert dict(results(out, "google"))["delete it"] == "FAIL"
    assert "remove event smoke-event-1-abcdefghijklmnop by hand" in out
    assert len(ctx["events"]) == 1


def test_a_revoked_refresh_token_fails_the_first_step_and_skips_what_depends_on_it() -> None:
    code, out, _ = run(GENV, bad_refresh=True)
    assert code == 1
    got = dict(results(out, "google"))
    assert got["OAuth refresh"] == "FAIL" and got["list calendars"] == "SKIP"
    assert "invalid_grant" in out
    assert "REFRESH-SECRET" not in out


def test_only_flag_limits_the_providers() -> None:
    code, out, _ = run({**GENV, **MENV}, ["--only", "microsoft"])
    assert code == 0 and not [ln for ln in out.splitlines() if ln.startswith("google")]
    assert results(out, "microsoft")


def test_connect_needs_a_client_id_and_never_starts_a_browser_without_it() -> None:
    http, _ = fake_world()
    lines: list[str] = []
    assert smoke.main(["--connect", "google"], env={}, http=http, out=lines.append) == 2
    assert "GOOGLE_CLIENT_ID" in lines[0]
    assert smoke.main(["--connect", "microsoft"], env={}, http=http, out=lines.append) == 2


def test_connect_prints_an_export_line_for_the_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from tt_client.integrations.oauth import Token

    seen: dict[str, Any] = {}

    class FakeAuth:
        def __init__(self, http: Any, cfg: Any, vault: Any) -> None:
            seen["scopes"] = cfg.scopes

        def connect(self, open_browser: Callable[[str], Any]) -> Token:
            return Token("a", "1//NEW-REFRESH", 0.0)

    monkeypatch.setattr(smoke, "Authorizer", FakeAuth)
    lines: list[str] = []
    assert (
        smoke.main(
            ["--connect", "google"], env={"GOOGLE_CLIENT_ID": "cid"}, http=fake_world()[0], out=lines.append
        )
        == 0
    )
    assert lines == [
        "export GOOGLE_REFRESH_TOKEN='1//NEW-REFRESH'   # keep it out of shell history and version control"
    ]
    assert any("calendar.events" in s for s in seen["scopes"]) and any(
        "readonly" in s for s in seen["scopes"]
    )
