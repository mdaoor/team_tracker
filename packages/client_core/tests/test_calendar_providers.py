"""Google and Microsoft adapters against recorded-shape fixtures: pagination, tokens, normalisation, Teams links (Phase 15 §15.3)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from calfx import Calls, client, google_handler, json_response, load, microsoft_handler
from tt_client.integrations import google as g
from tt_client.integrations import microsoft as ms
from tt_client.integrations.base import AuthRequired, ProviderError, RateLimited
from tt_client.integrations.oauth import (
    Authorizer,
    LoopbackListener,
    Token,
    TokenVault,
    authorize_url,
    exchange_code,
    make_pkce,
    parse_redirect,
)
from tt_client.secrets import MemoryStore

WINDOW = (datetime(2026, 10, 19, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC))
ME = "mahmoud@acme.com"


def connected_google(
    state: dict[str, Any] | None = None,
) -> tuple[g.GoogleCalendar, Calls, MemoryStore, dict[str, Any]]:
    state = {} if state is None else state
    calls, secrets = Calls(), MemoryStore()
    prov = g.GoogleCalendar(
        client(google_handler(state), calls), secrets, g.oauth_config("cid.apps.googleusercontent.com", "sec")
    )
    prov.vault.save(Token("ya29.GOOGLE-ACCESS-TOKEN-1", "1//GOOGLE-REFRESH-TOKEN", time.time() + 3000))
    return prov, calls, secrets, state


def connected_ms(
    state: dict[str, Any] | None = None, attendance: bool = False
) -> tuple[ms.MicrosoftCalendar, Calls, MemoryStore, dict[str, Any]]:
    state = {} if state is None else state
    calls, secrets = Calls(), MemoryStore()
    prov = ms.MicrosoftCalendar(
        client(microsoft_handler(state), calls), secrets, ms.oauth_config("ms-client", attendance=attendance)
    )
    prov.vault.save(Token("EwB-MS-ACCESS-TOKEN-1", "M.R3_BAY.MS-REFRESH-TOKEN", time.time() + 3000))
    return prov, calls, secrets, state


# ---------------------------------------------------------------------------------------------------- PKCE and OAuth plumbing
def test_pkce_pair_is_s256_and_urls_carry_it() -> None:
    import base64
    import hashlib

    verifier, challenge = make_pkce()
    assert 43 <= len(verifier) <= 128
    assert (
        challenge
        == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    url = authorize_url(g.oauth_config("cid"), state="st", challenge=challenge)
    assert (
        "code_challenge_method=S256" in url
        and f"code_challenge={challenge}" in url
        and "access_type=offline" in url
    )
    assert "calendar.readonly" in url
    murl = authorize_url(
        ms.oauth_config("mid", tenant="contoso.onmicrosoft.com", attendance=True),
        state="st",
        challenge=challenge,
    )
    assert "login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/authorize" in murl
    assert "Calendars.Read" in murl and "offline_access" in murl and "OnlineMeetings.Read" in murl


def test_redirect_parsing_checks_state_and_errors() -> None:
    assert parse_redirect("http://127.0.0.1:8765/callback?code=abc&state=s1", "s1") == "abc"
    with pytest.raises(AuthRequired, match="state"):
        parse_redirect("http://127.0.0.1:8765/callback?code=abc&state=other", "s1")
    with pytest.raises(AuthRequired, match="access_denied"):
        parse_redirect("http://127.0.0.1:8765/callback?error=access_denied&state=s1", "s1")
    with pytest.raises(AuthRequired, match="no code"):
        parse_redirect("http://127.0.0.1:8765/callback?state=s1", "s1")


def test_code_exchange_sends_the_verifier_and_stores_nothing_itself() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(x.split("=", 1) for x in req.content.decode().split("&")))
        return json_response(load("google/token.json"))

    tok = exchange_code(client(handler), g.oauth_config("cid", "sec"), "the-code", "the-verifier", now=1000.0)
    assert (
        seen["code_verifier"] == "the-verifier"
        and seen["grant_type"] == "authorization_code"
        and seen["client_secret"] == "sec"
    )
    assert tok.access_token.startswith("ya29.") and tok.expires_at == 1000.0 + 3599


def test_loopback_listener_catches_the_redirect_on_127_0_0_1() -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    listener = LoopbackListener(f"http://127.0.0.1:{port}/callback").start()
    httpx.get(f"http://127.0.0.1:{port}/callback?code=zzz&state=st")
    assert listener.wait(5) == f"http://127.0.0.1:{port}/callback?code=zzz&state=st"
    with pytest.raises(ProviderError, match="loopback"):
        LoopbackListener("https://evil.example/callback")


def test_connect_runs_the_whole_flow_with_an_injected_browser() -> None:
    secrets = MemoryStore()
    cfg = g.oauth_config("cid", redirect_uri="http://127.0.0.1:1/callback")
    http = client(lambda r: json_response(load("google/token.json")))
    opened: list[str] = []

    class Waiter:
        def __init__(self) -> None:
            self.url = ""

        def start(self) -> Waiter:
            return self

        def wait(self, timeout: float) -> str:
            state = [p.split("=")[1] for p in opened[0].split("?")[1].split("&") if p.startswith("state=")][0]
            return f"http://127.0.0.1:1/callback?code=c&state={state}"

    Authorizer(http, cfg, TokenVault(secrets, "google")).connect(opened.append, listen=Waiter())
    assert TokenVault(secrets, "google").load().refresh_token == "1//GOOGLE-REFRESH-TOKEN"  # type: ignore[union-attr]


def test_an_expired_token_is_refreshed_once_and_saved_and_a_revoked_one_asks_to_reconnect() -> None:
    prov, calls, secrets, state = connected_google()
    prov.vault.save(Token("old", "1//GOOGLE-REFRESH-TOKEN", time.time() - 5))
    state["valid_token"] = "ya29.GOOGLE-ACCESS-TOKEN-2"
    assert [c.id for c in prov.list_calendars()][0] == ME
    assert state["token_calls"] == 1
    assert prov.vault.load().access_token == "ya29.GOOGLE-ACCESS-TOKEN-2"  # type: ignore[union-attr]
    # revoked: the provider rejects even the refreshed token
    state["valid_token"] = "something-else"
    with pytest.raises(AuthRequired, match="connect"):
        prov.list_calendars()
    # never connected
    fresh = g.GoogleCalendar(client(google_handler({})), MemoryStore(), g.oauth_config("cid"))
    with pytest.raises(AuthRequired, match="tt cal connect"):
        fresh.list_calendars()


# ---------------------------------------------------------------------------------------------------- Google
def test_google_lists_calendars_and_marks_unselected_ones() -> None:
    prov, *_ = connected_google()
    cals = prov.list_calendars()
    assert [(c.id, c.primary, c.selected) for c in cals] == [
        (ME, True, True),
        ("team-web@group.calendar.google.com", False, True),
        ("holidays@group.v.calendar.google.com", False, False),
    ]


def test_google_full_sync_follows_pages_and_returns_a_sync_token() -> None:
    prov, calls, *_ = connected_google()
    page = prov.sync(ME, cursor=None, window=WINDOW, me=ME)
    assert page.full and page.cursor == {"sync_token": "SYNC-1"}
    assert len(page.events) == 10 and any("pageToken=PAGE2" in u for u in calls.urls())
    first = calls.requests[0].url.params
    assert first["timeMin"].endswith("Z") and first["singleEvents"] == "true"
    by = {e.event_id: e for e in page.events}
    sprint = by["g-evt-1"]
    assert (sprint.title, sprint.series_id, sprint.response) == (
        "Sprint planning",
        "g-series-sprint",
        "accepted",
    )
    assert sprint.attendees == ["sara@acme.com", "ali@acme.com"]  # me is not an attendee of my own meeting
    assert sprint.online_url == "https://meet.google.com/abc-defg-hij"
    assert sprint.start == datetime(2026, 10, 26, 6, 0, tzinfo=UTC) and sprint.instance_start == sprint.start
    assert by["g-evt-3"].response == "declined" and by["g-evt-4"].all_day and by["g-evt-5"].is_private
    assert by["g-evt-6"].event_type == "focusTime" and by["g-evt-7"].event_type == "outOfOffice"
    assert by["g-evt-8"].response == "tentative" and by["g-evt-9"].removed


def test_google_incremental_sync_uses_the_token_and_a_410_falls_back_to_a_full_sync() -> None:
    prov, calls, _, state = connected_google()
    inc = prov.sync(ME, cursor={"sync_token": "SYNC-1"}, window=WINDOW, me=ME)
    assert not inc.full and inc.cursor == {"sync_token": "SYNC-2"}
    assert (
        "syncToken=SYNC-1" in calls.urls()[-1] and "timeMin" not in calls.urls()[-1]
    )  # the API forbids mixing them
    assert [e.event_id for e in inc.events] == ["g-evt-1", "g-evt-11", "g-evt-8", "g-evt-10"]
    state["gone"] = True
    again = prov.sync(ME, cursor={"sync_token": "SYNC-1"}, window=WINDOW, me=ME)
    assert again.full and again.cursor == {"sync_token": "SYNC-1"}  # fell back to the window


def test_google_rate_limit_carries_retry_after() -> None:
    prov, _, _, state = connected_google({"rate_limit": True})
    with pytest.raises(RateLimited) as exc:
        prov.sync(ME, cursor=None, window=WINDOW)
    assert exc.value.retry_after == 120.0


# ---------------------------------------------------------------------------------------------------- Microsoft
def test_microsoft_lists_calendars() -> None:
    prov, *_ = connected_ms()
    assert [(c.id, c.primary) for c in prov.list_calendars()] == [
        ("AAMkAD-cal-1", True),
        ("AAMkAD-cal-2", False),
    ]


def test_microsoft_delta_follows_next_links_and_keeps_the_delta_link() -> None:
    prov, calls, *_ = connected_ms()
    page = prov.sync("AAMkAD-cal-1", cursor=None, window=WINDOW, me=ME)
    assert page.full and page.cursor["delta_link"].endswith("$deltatoken=DELTA-1")
    assert len(page.events) == 9
    first = calls.requests[0]
    assert (
        first.url.params["startDateTime"].endswith("Z")
        and 'outlook.timezone="UTC"' in first.headers["Prefer"]
    )
    by = {e.event_id: e for e in page.events}
    teams = by["ms-1"]
    assert teams.online_url.startswith("https://teams.microsoft.com/l/meetup-join/")  # type: ignore[union-attr]
    assert teams.series_id == "ms-series-sprint" and teams.start == datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    assert teams.attendees == ["sara@acme.com"]  # lower-cased, and not me
    assert by["ms-3"].response == "declined" and by["ms-4"].all_day and by["ms-5"].is_private
    assert (
        by["ms-6"].event_type == "outOfOffice" and by["ms-7"].removed and by["ms-8"].event_type == "focusTime"
    )
    assert by["ms-2"].organizer == "sara@acme.com"
    inc = prov.sync("AAMkAD-cal-1", cursor=page.cursor, window=WINDOW, me=ME)
    assert not inc.full and inc.cursor["delta_link"].endswith("$deltatoken=DELTA-2")
    assert [(e.event_id, e.removed) for e in inc.events] == [
        ("ms-9", False),
        ("ms-10", False),
        ("ms-2", True),
    ]
    assert "deltatoken=DELTA-1" in calls.urls()[-1] and "startDateTime" not in calls.urls()[-1]


def test_teams_attendance_minutes_when_consented_and_none_when_not() -> None:
    prov, _, _, state = connected_ms(attendance=True)
    ev = next(
        e for e in prov.sync("AAMkAD-cal-1", cursor=None, window=WINDOW, me=ME).events if e.event_id == "ms-1"
    )
    assert prov.attended_minutes(ev, ME) == 45
    assert prov.attended_minutes(ev, "someone@else.com") is None
    state["no_consent"] = True  # admin consent missing: quietly nothing, never an error in the plan
    assert prov.attended_minutes(ev, ME) is None
    off, *_ = connected_ms(attendance=False)
    assert off.attended_minutes(ev, ME) is None


def test_microsoft_token_refresh_and_failure_messages_do_not_leak_tokens() -> None:
    prov, calls, secrets, state = connected_ms()
    prov.vault.save(Token("stale", "M.R3_BAY.MS-REFRESH-TOKEN", time.time() - 1))
    state["valid_token"] = "EwB-MS-ACCESS-TOKEN-2"
    prov.list_calendars()
    assert prov.vault.load().refresh_token == "M.R3_BAY.MS-REFRESH-TOKEN-2"  # type: ignore[union-attr]  # rotated refresh token is kept
    state["valid_token"] = "nope"
    with pytest.raises(AuthRequired) as exc:
        prov.list_calendars()
    assert "EwB" not in str(exc.value) and "MS-REFRESH" not in str(exc.value)


def test_network_errors_become_provider_errors() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    prov = g.GoogleCalendar(client(boom), MemoryStore(), g.oauth_config("cid"))
    prov.vault.save(Token("ya29.x", "r", time.time() + 3000))
    with pytest.raises(ProviderError, match="network error"):
        prov.list_calendars()


def test_expiry_margin() -> None:
    assert Token("a", "r", 1000.0).expired(now=950.0) and not Token("a", "r", 1000.0).expired(now=900.0)
    assert datetime.now(UTC) - timedelta(seconds=1) < datetime.now(UTC)
