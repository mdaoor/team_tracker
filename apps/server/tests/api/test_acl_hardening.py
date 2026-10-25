"""Phase 12 §5: rate limiting (capture per credential, sign-in per address) and the security headers/CSP on every response."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tt_server.main import create_app
from tt_server.ratelimit import SlidingWindow, credential_key
from tt_server.settings import Settings

from tests.api.conftest import Api

ROUTERS_COVERED = ("auth", "webauth")


@pytest.fixture
async def limited(
    api: Api, engine: AsyncEngine, migrated_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> httpx.AsyncClient:
    s = Settings(
        database_url=migrated_url,
        log_json=False,
        log_level="WARNING",
        dev_auth=True,
        rate_capture_per_min=3,
        rate_auth_per_min=2,
    )
    app = create_app(s, engine=engine, session_factory=session_factory)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def batch(handle: str = "@mahmoud") -> dict:
    return {
        "workspace": "acme",
        "messages": [
            {
                "actor": handle,
                "at": "2026-10-23T10:00:00+03:00",
                "client_id": str(uuid.uuid4()),
                "action": "note",
                "note": "x",
            }
        ],
    }


async def test_capture_is_limited_per_credential_with_retry_after(limited: httpx.AsyncClient) -> None:
    url = "/api/v1/w/acme/capture/batches"
    for _ in range(3):
        assert (
            await limited.post(url, json=batch(), headers={"X-Debug-Actor": "@mahmoud"})
        ).status_code == 200
    r = await limited.post(url, json=batch(), headers={"X-Debug-Actor": "@mahmoud"})
    assert (
        r.status_code == 429
        and r.json()["error"]["code"] == "rate_limited"
        and int(r.headers["retry-after"]) >= 1
    )
    # another credential has its own bucket; reads are never limited
    assert (
        await limited.post(url, json=batch("@sara"), headers={"X-Debug-Actor": "@sara"})
    ).status_code == 200
    for _ in range(10):
        assert (
            await limited.get("/api/v1/w/acme/me", headers={"X-Debug-Actor": "@mahmoud"})
        ).status_code == 200


async def test_sign_in_endpoints_are_limited_per_address(limited: httpx.AsyncClient) -> None:
    codes = [
        (
            await limited.post(
                "/auth/ldap/login", json={"workspace": "acme", "username": "u", "password": "p"}
            )
        ).status_code
        for _ in range(4)
    ]
    assert codes[:2] == [404, 404] and codes[2:] == [
        429,
        429,
    ]  # LDAP is not configured here; the limiter sits in front regardless


def test_sliding_window_unit() -> None:
    t = [0.0]
    w = SlidingWindow(2, 10.0, clock=lambda: t[0])
    assert w.hit("a") is None and w.hit("a") is None
    assert w.hit("a") == pytest.approx(10.0)
    t[0] = 5.0
    assert w.hit("a") == pytest.approx(5.0) and w.hit("b") is None
    t[0] = 10.5
    assert w.hit("a") is None  # the first hit left the window
    t[0] = 200.0
    for i in range(50):
        w.hit(f"k{i}")
    assert len(w._hits) <= 51  # idle keys are swept  # noqa: SLF001


def test_credential_keys_do_not_leak_secrets() -> None:
    k = credential_key("Bearer secret-token", None, None, "1.2.3.4")
    assert (
        "secret" not in k and k.startswith("t") and k != credential_key("Bearer other", None, None, "1.2.3.4")
    )
    assert credential_key(None, "cookie", None, None).startswith("s") and credential_key(
        None, None, None, "1.2.3.4"
    ).startswith("a")
    assert credential_key(None, None, None, None) == "anon"


async def test_every_response_carries_the_security_headers(api: Api) -> None:
    r = await api.call("GET", "/me")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    e = await api.c.get("/api/v1/w/nope/me", headers={"X-Debug-Actor": "@x"})  # error responses too
    assert e.status_code == 404 and e.headers["x-content-type-options"] == "nosniff"


async def test_html_shells_get_a_nonce_csp_without_remote_origins(api: Api) -> None:
    await api.call("POST", "/presentations", json={"title": "D", "slides": [{"kind": "kpis"}]})
    pid = (await api.call("GET", "/presentations")).json()["data"][0]["id"]
    r = await api.c.get(f"/p/acme/{pid}", headers={"X-Debug-Actor": "@mahmoud"})
    assert r.status_code in (200, 404, 503)
    if r.status_code == 200:
        csp = r.headers["content-security-policy"]
        assert (
            "default-src 'none'" in csp and "http://" not in csp and "https://" not in csp and "*" not in csp
        )
        # R3.7: scripts need the per-response nonce; no 'unsafe-inline' for script-src
        script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
        assert "'unsafe-inline'" not in script_src and "'nonce-" in script_src
        nonce = script_src.split("'nonce-")[1].split("'")[0]
        assert f'<script nonce="{nonce}"' in r.text and "<script>" not in r.text
        # R1.2: the same-origin builder may frame its own preview, nobody else
        assert "frame-ancestors 'self'" in csp and r.headers["x-frame-options"] == "SAMEORIGIN"


async def test_each_shell_response_gets_a_fresh_nonce(api: Api) -> None:
    await api.call("POST", "/presentations", json={"title": "D", "slides": [{"kind": "kpis"}]})
    pid = (await api.call("GET", "/presentations")).json()["data"][0]["id"]
    h = {"X-Debug-Actor": "@mahmoud"}
    a = await api.c.get(f"/p/acme/{pid}", headers=h)
    b = await api.c.get(f"/p/acme/{pid}", headers=h)
    if a.status_code == 200:
        assert a.headers["content-security-policy"] != b.headers["content-security-policy"]


async def test_share_link_shell_cannot_be_framed(api: Api) -> None:
    r = await api.c.get("/p/t/not-a-token")
    if r.status_code == 200:
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"


def test_bundle_has_only_tag_script_openers() -> None:
    from tt_server.api.routers.presentations import BUNDLE

    if BUNDLE.exists():
        html = BUNDLE.read_text()
        import re

        assert html.count("<script") == len(re.findall(r"<script(?=[\s>])", html))


async def test_metrics_expose_red_by_route_template_and_business_gauges(api: Api) -> None:
    await api.call("GET", "/me")
    await api.call("GET", "/items/NOPE-1")
    await api.send(action="note", note="x", project="WEB")
    text = (await api.c.get("/metrics")).text
    assert 'tt_http_requests_total{method="GET",route="/api/v1/w/{ws}/me",status="2xx"}' in text
    assert 'route="/api/v1/w/{ws}/items/{id_or_key}",status="4xx"' in text  # template, not the concrete path
    assert "NOPE-1" not in text
    assert "tt_http_request_duration_seconds_bucket" in text and "tt_capture_batches_total 1.0" in text
    for g in (
        "tt_review_queue_open",
        "tt_violations_open",
        "tt_jobs_todo",
        "tt_jobs_oldest_todo_age_seconds",
        "tt_events_last_5m",
    ):
        assert f"\n{g} " in text, g


async def test_metrics_are_off_outside_dev_and_need_the_token_when_set(
    engine: AsyncEngine, migrated_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    base = dict(database_url=migrated_url, log_json=False, log_level="WARNING", session_secret="x" * 40, secret_key="k" * 40)
    off = create_app(Settings(env="prod", **base), engine=engine, session_factory=session_factory)  # type: ignore[arg-type]
    on = create_app(
        Settings(env="prod", metrics_token="scrape-me", **base),
        engine=engine,
        session_factory=session_factory,
    )  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=off), base_url="http://t") as c:
        assert (await c.get("/metrics")).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=on), base_url="http://t") as c:
        assert (await c.get("/metrics")).status_code == 401
        assert (await c.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer scrape-me"})
        assert ok.status_code == 200 and "tt_jobs_todo" in ok.text


def _proxy_app(engine, migrated_url, session_factory, trusted: str):  # type: ignore[no-untyped-def]
    s = Settings(
        database_url=migrated_url, log_json=False, log_level="WARNING", dev_auth=True,
        rate_capture_per_min=0, rate_auth_per_min=2, trusted_proxies=trusted,
    )  # fmt: skip
    app = create_app(s, engine=engine, session_factory=session_factory)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _signins(c: httpx.AsyncClient, forwarded: list[str]) -> list[int]:
    out = []
    for ip in forwarded:
        r = await c.post(
            "/auth/ldap/login", json={"workspace": "acme", "username": "x", "password": "y"},
            headers={"X-Forwarded-For": ip},
        )  # fmt: skip
        out.append(r.status_code)
    return out


async def test_forwarded_for_is_believed_only_from_a_trusted_proxy(
    api: Api, engine: AsyncEngine, migrated_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Review T1: behind the documented ingress every user shared one bucket (peer = the proxy)."""
    behind = _proxy_app(engine, migrated_url, session_factory, trusted="127.0.0.1")
    codes = await _signins(behind, ["198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4"])
    assert 429 not in codes, codes  # four different people, four buckets of two
    codes = await _signins(behind, ["198.51.100.9"] * 4)
    assert codes[-1] == 429  # but one person still runs out
    stranger = _proxy_app(engine, migrated_url, session_factory, trusted="")
    codes = await _signins(stranger, ["198.51.100.1", "198.51.100.2", "198.51.100.3"])
    assert codes[-1] == 429, codes  # a header from an untrusted peer cannot choose the bucket


def test_client_ip_walks_past_trusted_hops() -> None:
    from tt_server.ratelimit import client_ip

    s = Settings(trusted_proxies="10.0.0.0/8,192.168.1.5")
    assert client_ip(s, "10.0.0.7", "203.0.113.9, 10.0.0.3") == "203.0.113.9"
    assert client_ip(s, "10.0.0.7", "203.0.113.9") == "203.0.113.9"
    assert client_ip(s, "203.0.113.50", "1.2.3.4") == "203.0.113.50"  # peer not trusted: header ignored
    assert client_ip(s, "10.0.0.7", None) == "10.0.0.7"
    assert client_ip(s, "10.0.0.7", "10.0.0.2, 10.0.0.3") == "10.0.0.2"  # all hops trusted: the first one
    assert client_ip(Settings(), "10.0.0.7", "1.2.3.4") == "10.0.0.7"
