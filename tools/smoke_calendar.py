#!/usr/bin/env python3
"""Live-account smoke test for the Google Calendar and Microsoft Graph adapters (Phase 15, handover "outstanding real-account smoke").

Everything else in the repo exercises the adapters against fakes. This script talks to the REAL APIs with a dedicated TEST account and prints a
pass/fail table. It does nothing (exit 0, "skipped: no credentials") unless credentials are in the environment, so it is safe in CI.

    uv run python tools/smoke_calendar.py               # run what the environment allows
    uv run python tools/smoke_calendar.py --connect google|microsoft   # one-time consent: prints a refresh token for the env var

Environment (use a throw-away test account, never a real person's calendar):
    GOOGLE_CLIENT_ID  GOOGLE_REFRESH_TOKEN  [GOOGLE_CLIENT_SECRET]  [GOOGLE_SMOKE_CALENDAR_ID=primary]
    MS_CLIENT_ID      MS_REFRESH_TOKEN      [MS_TENANT=common]

Per provider the checks are: OAuth refresh, list calendars, read one window, incremental read with the stored cursor, refresh with the WRITE
scope, create a test event, read it back, delete it, confirm it is gone. Steps 5-9 need a refresh token consented with write scope
(`--connect`); a read-only token yields SKIP for them, not FAIL. The created event is titled "[tt-smoke] ..." and is always deleted; if the delete
fails the table prints the event id so it can be removed by hand. No token is ever printed.

Exit status: 0 when nothing failed (skips included), 1 when any check failed, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from tt_client.integrations import google as g
from tt_client.integrations import microsoft as ms
from tt_client.integrations.base import CalendarProvider, ProviderError
from tt_client.integrations.oauth import Authorizer, OAuthConfig, Token, TokenVault
from tt_client.secrets import MemoryStore

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
GOOGLE_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
MS_WRITE_SCOPES = ("offline_access", "Calendars.ReadWrite")
NO_CREDENTIALS = "skipped: no credentials (set GOOGLE_CLIENT_ID + GOOGLE_REFRESH_TOKEN and/or MS_CLIENT_ID + MS_REFRESH_TOKEN)"


@dataclass
class Check:
    provider: str
    name: str
    status: str
    detail: str = ""
    ms: int = 0


class Runner:
    """Collects checks; a failing check does not stop the independent ones, a failing prerequisite skips what depends on it."""

    def __init__(self, provider: str) -> None:
        self.provider, self.checks = provider, []  # type: ignore[var-annotated]

    def step(self, name: str, fn: Callable[[], str]) -> bool:
        t0 = time.perf_counter()
        try:
            detail = fn()
            status = PASS
        except SmokeSkip as exc:
            detail, status = str(exc), SKIP
        except (ProviderError, httpx.HTTPError, KeyError, ValueError, AssertionError) as exc:
            detail, status = f"{type(exc).__name__}: {exc}"[:300], FAIL
        self.checks.append(
            Check(self.provider, name, status, detail, round((time.perf_counter() - t0) * 1000))
        )
        return status == PASS

    def skip(self, name: str, why: str) -> None:
        self.checks.append(Check(self.provider, name, SKIP, why))


class SmokeSkip(Exception):
    """A step that cannot run with this account/token (not a failure of the adapter)."""


def _window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=7), now + timedelta(days=14)


def _test_slot(now: datetime) -> tuple[datetime, datetime]:
    start = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
    return start, start + timedelta(minutes=15)


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_checks(r: Runner, prov: CalendarProvider, calendar_hint: str | None, now: datetime) -> str | None:
    """Steps 1-4 with the adapter under test. Returns the calendar id used (for the write steps)."""
    auth: Authorizer = prov.auth  # type: ignore[attr-defined]
    chosen: dict[str, str] = {}

    def refresh() -> str:
        auth.access_token(force_refresh=True)
        tok = auth.vault.load()
        assert tok is not None and tok.access_token, "no access token came back"
        return f"new access token, valid for {max(0, int(tok.expires_at - time.time()))} s"

    ok = r.step("OAuth refresh", refresh)
    if not ok:
        for n in ("list calendars", "read one window", "incremental read"):
            r.skip(n, "needs a working token")
        return None
    cals: list[Any] = []

    def listing() -> str:
        cals.extend(prov.list_calendars())
        assert cals, "the account has no calendars"
        pick = next((c for c in cals if calendar_hint and calendar_hint in (c.id, c.name)), None) or next(
            (c for c in cals if c.primary), cals[0]
        )
        chosen["id"] = pick.id
        return f"{len(cals)} calendar(s); using {pick.name!r}"

    r.step("list calendars", listing)
    if "id" not in chosen:
        r.skip("read one window", "needs a calendar")
        r.skip("incremental read", "needs a calendar")
        return None
    cursor: dict[str, Any] = {}

    def window() -> str:
        page = prov.sync(chosen["id"], cursor=None, window=_window(now), me=None)
        cursor.update(page.cursor)
        assert page.full, "first read should be a full read"
        assert any(cursor.values()), "no sync cursor came back"
        return f"{len(page.events)} event(s) in -7d..+14d; cursor stored"

    r.step("read one window", window)

    def incremental() -> str:
        if not cursor:
            raise SmokeSkip("needs the first read")
        page = prov.sync(chosen["id"], cursor=cursor, window=_window(now), me=None)
        return f"{len(page.events)} change(s) since the first read" + (
            "" if not page.full else " (provider forced a full read)"
        )

    r.step("incremental read", incremental)
    return chosen["id"]


# --------------------------------------------------------------------------------------------------------------------- Google
def run_google(env: Mapping[str, str], http: httpx.Client, now: datetime) -> list[Check]:
    r = Runner("google")
    cid, refresh = env.get("GOOGLE_CLIENT_ID"), env.get("GOOGLE_REFRESH_TOKEN")
    if not cid or not refresh:
        r.skip("all", "GOOGLE_CLIENT_ID / GOOGLE_REFRESH_TOKEN not set")
        return r.checks
    secret = env.get("GOOGLE_CLIENT_SECRET") or None
    store = MemoryStore()
    prov = g.GoogleCalendar(http, store, g.oauth_config(cid, secret))
    prov.vault.save(Token("", refresh, 0.0))
    cal = _read_checks(r, prov, env.get("GOOGLE_SMOKE_CALENDAR_ID") or None, now)

    write_cfg = OAuthConfig(**{**g.oauth_config(cid, secret).__dict__, "scopes": (GOOGLE_WRITE_SCOPE,)})
    _write_checks(
        r,
        http,
        write_cfg,
        refresh,
        now,
        calendar=cal or env.get("GOOGLE_SMOKE_CALENDAR_ID") or "primary",
        create=lambda h, token, start, end, title, cal_id: _google_create(
            h, token, cal_id, start, end, title
        ),
        fetch=lambda h, token, ev, cal_id: h.get(
            f"{g.API}/calendars/{g._q(cal_id)}/events/{ev}", headers=_bearer(token)
        ),
        delete=lambda h, token, ev, cal_id: h.delete(
            f"{g.API}/calendars/{g._q(cal_id)}/events/{ev}", headers=_bearer(token)
        ),
        gone=lambda resp: resp.status_code in (404, 410) or resp.json().get("status") == "cancelled",
    )
    return r.checks


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _google_create(
    h: httpx.Client, token: str, cal_id: str, start: datetime, end: datetime, title: str
) -> httpx.Response:
    body = {
        "summary": title,
        "start": {"dateTime": _iso(start), "timeZone": "UTC"},
        "end": {"dateTime": _iso(end), "timeZone": "UTC"},
        "transparency": "transparent",
        "visibility": "private",
        "description": "Created by Team Tracker tools/smoke_calendar.py; safe to delete.",
    }
    return h.post(f"{g.API}/calendars/{g._q(cal_id)}/events", json=body, headers=_bearer(token))


# ------------------------------------------------------------------------------------------------------------------ Microsoft
def run_microsoft(env: Mapping[str, str], http: httpx.Client, now: datetime) -> list[Check]:
    r = Runner("microsoft")
    cid, refresh = env.get("MS_CLIENT_ID"), env.get("MS_REFRESH_TOKEN")
    if not cid or not refresh:
        r.skip("all", "MS_CLIENT_ID / MS_REFRESH_TOKEN not set")
        return r.checks
    tenant = env.get("MS_TENANT") or "common"
    store = MemoryStore()
    prov = ms.MicrosoftCalendar(http, store, ms.oauth_config(cid, tenant=tenant))
    prov.vault.save(Token("", refresh, 0.0))
    _read_checks(r, prov, None, now)

    write_cfg = OAuthConfig(**{**ms.oauth_config(cid, tenant=tenant).__dict__, "scopes": MS_WRITE_SCOPES})
    _write_checks(
        r,
        http,
        write_cfg,
        refresh,
        now,
        calendar="",
        create=lambda h, token, start, end, title, _c: _ms_create(h, token, start, end, title),
        fetch=lambda h, token, ev, _c: h.get(f"{ms.GRAPH}/me/events/{ev}", headers=_bearer(token)),
        delete=lambda h, token, ev, _c: h.delete(f"{ms.GRAPH}/me/events/{ev}", headers=_bearer(token)),
        gone=lambda resp: resp.status_code in (404, 410),
    )
    return r.checks


def _ms_create(h: httpx.Client, token: str, start: datetime, end: datetime, title: str) -> httpx.Response:
    body = {
        "subject": title,
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
        "showAs": "free",
        "sensitivity": "private",
        "body": {
            "contentType": "text",
            "content": "Created by Team Tracker tools/smoke_calendar.py; safe to delete.",
        },
    }
    return h.post(f"{ms.GRAPH}/me/events", json=body, headers=_bearer(token))


# ----------------------------------------------------------------------------------------------------------- shared write path
def _write_checks(
    r: Runner,
    http: httpx.Client,
    cfg: OAuthConfig,
    refresh: str,
    now: datetime,
    *,
    calendar: str,
    create: Callable[..., httpx.Response],
    fetch: Callable[..., httpx.Response],
    delete: Callable[..., httpx.Response],
    gone: Callable[[httpx.Response], bool],
) -> None:
    vault = TokenVault(MemoryStore(), r.provider)
    vault.save(Token("", refresh, 0.0))
    auth = Authorizer(http, cfg, vault)
    state: dict[str, str] = {}

    def write_refresh() -> str:
        try:
            state["token"] = auth.access_token(force_refresh=True)
        except ProviderError as exc:
            raise SmokeSkip(
                f"this refresh token has no write scope ({exc}); run with --connect {r.provider} to consent"
            ) from exc
        return "access token with the write scope"

    if not r.step("refresh with write scope", write_refresh):
        for n in ("create test event", "read it back", "delete it", "confirm it is gone"):
            r.skip(n, "needs the write scope")
        return
    start, end = _test_slot(now)
    title = f"[tt-smoke] Team Tracker smoke test {now:%Y-%m-%d %H:%M}Z"

    def do_create() -> str:
        resp = create(http, state["token"], start, end, title, calendar)
        if resp.status_code in (401, 403):
            raise SmokeSkip(
                f"the account refused to create events (HTTP {resp.status_code}): read-only calendar or consent"
            )
        assert resp.status_code in (200, 201), f"create answered HTTP {resp.status_code}"
        state["id"] = str(resp.json()["id"])
        return f"created {state['id'][:12]}... at {start:%Y-%m-%d %H:%M}Z"

    created = r.step("create test event", do_create)
    if not created:
        for n in ("read it back", "delete it", "confirm it is gone"):
            r.skip(n, "no event was created")
        return

    def read_back() -> str:
        resp = fetch(http, state["token"], state["id"], calendar)
        assert resp.status_code == 200, f"GET answered HTTP {resp.status_code}"
        body = resp.json()
        got = body.get("summary") or body.get("subject")
        assert got == title, f"title mismatch: {got!r}"
        return "title round-trips"

    try:
        r.step("read it back", read_back)
    finally:
        deleted = r.step("delete it", lambda: _delete(delete, http, state, calendar))
    if deleted:
        r.step("confirm it is gone", lambda: _confirm_gone(fetch, gone, http, state, calendar))
    else:
        r.skip("confirm it is gone", f"delete failed: remove event {state['id']} by hand ({title!r})")


def _delete(
    delete: Callable[..., httpx.Response], http: httpx.Client, state: dict[str, str], calendar: str
) -> str:
    resp = delete(http, state["token"], state["id"], calendar)
    assert resp.status_code in (200, 204), f"DELETE answered HTTP {resp.status_code}"
    return "deleted"


def _confirm_gone(
    fetch: Callable[..., httpx.Response],
    gone: Callable[[httpx.Response], bool],
    http: httpx.Client,
    state: dict[str, str],
    calendar: str,
) -> str:
    resp = fetch(http, state["token"], state["id"], calendar)
    assert gone(resp), f"the event is still readable (HTTP {resp.status_code})"
    return f"HTTP {resp.status_code}: gone"


# -------------------------------------------------------------------------------------------------------------------- output
def render(checks: list[Check]) -> str:
    rows = [
        ("provider", "check", "result", "ms", "detail"),
        *[(c.provider, c.name, c.status, str(c.ms), c.detail) for c in checks],
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    out = []
    for i, r in enumerate(rows):
        out.append("  ".join(r[j].ljust(widths[j]) for j in range(4)) + "  " + r[4])
        if i == 0:
            out.append("  ".join("-" * w for w in widths) + "  ------")
    return "\n".join(out)


def connect(
    provider: str, env: Mapping[str, str], http: httpx.Client, out: Callable[[str], None] = print
) -> int:
    """One-time consent with the write scope; prints the refresh token to export. Interactive (opens a browser, loopback redirect)."""
    import webbrowser

    if provider == "google":
        cid = env.get("GOOGLE_CLIENT_ID")
        if not cid:
            out("set GOOGLE_CLIENT_ID (a Desktop-app OAuth client of the test project) first")
            return 2
        cfg = g.oauth_config(cid, env.get("GOOGLE_CLIENT_SECRET") or None)
        cfg = OAuthConfig(**{**cfg.__dict__, "scopes": (*g.SCOPES, GOOGLE_WRITE_SCOPE)})
        name = "GOOGLE_REFRESH_TOKEN"
    else:
        cid = env.get("MS_CLIENT_ID")
        if not cid:
            out("set MS_CLIENT_ID (a public-client app registration of the test tenant) first")
            return 2
        cfg = OAuthConfig(
            **{
                **ms.oauth_config(cid, tenant=env.get("MS_TENANT") or "common").__dict__,
                "scopes": (*MS_WRITE_SCOPES,),
            }
        )
        name = "MS_REFRESH_TOKEN"
    vault = TokenVault(MemoryStore(), provider)
    token = Authorizer(http, cfg, vault).connect(webbrowser.open)
    if not token.refresh_token:
        out(
            "the provider returned no refresh token (Google: revoke the app's access and retry; the flow asks for offline access)"
        )
        return 1
    out(f"export {name}='{token.refresh_token}'   # keep it out of shell history and version control")
    return 0


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    http: httpx.Client | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    out: Callable[[str], None] = print,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument(
        "--connect",
        choices=("google", "microsoft"),
        help="one-time consent (with write scope); prints a refresh token",
    )
    ap.add_argument("--only", choices=("google", "microsoft"), help="run one provider")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    client = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0))
    if args.connect:
        return connect(args.connect, env, client, out)
    wanted = [p for p in ("google", "microsoft") if args.only in (None, p)]
    have = {
        "google": bool(env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_REFRESH_TOKEN")),
        "microsoft": bool(env.get("MS_CLIENT_ID") and env.get("MS_REFRESH_TOKEN")),
    }
    if not any(have[p] for p in wanted):
        out(NO_CREDENTIALS)
        return 0
    now = clock()
    checks: list[Check] = []
    for p in wanted:
        checks += (run_google if p == "google" else run_microsoft)(env, client, now)
    out(render(checks))
    failed = [c for c in checks if c.status == FAIL]
    passed = sum(c.status == PASS for c in checks)
    out(f"\n{passed} passed, {sum(c.status == SKIP for c in checks)} skipped, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
