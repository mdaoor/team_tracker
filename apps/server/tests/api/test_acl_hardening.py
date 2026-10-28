"""Phase 12 §5: rate limiting (capture per credential, sign-in per address) and the security headers/CSP on every response."""

from __future__ import annotations

import uuid
from pathlib import Path

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
        rate_commands_per_min=5,
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


def _cmd(name: str) -> dict:
    return {
        "client_id": str(uuid.uuid4()),
        "actor": "@mahmoud",
        "at": "2026-10-25T10:00:00+03:00",
        "op": "tag.create",
        "args": {"name": name},
    }


async def test_commands_are_limited_per_command_across_all_three_entry_points(
    limited: httpx.AsyncClient,
) -> None:
    h = {"X-Debug-Actor": "@mahmoud"}
    base = "/api/v1/w/acme"
    r = await limited.post(
        f"{base}/commands", headers=h, json={"commands": [_cmd(f"a{i}") for i in range(3)]}
    )
    assert r.status_code == 200
    # two more via a capture batch still fit (3 + 2 = 5); the 6th command anywhere is refused
    b = {**batch(), "commands": [_cmd("b0"), _cmd("b1")]}
    assert (await limited.post(f"{base}/capture/batches", headers=h, json=b)).status_code == 200
    over = await limited.post(f"{base}/commands", headers=h, json={"commands": [_cmd("c0")]})
    assert over.status_code == 429 and over.json()["error"]["code"] == "rate_limited"
    assert int(over.headers["retry-after"]) >= 1
    over = await limited.post(
        f"{base}/capture/batches", headers=h, json={**batch(), "commands": [_cmd("c1")]}
    )
    assert over.status_code == 429 and int(over.headers["retry-after"]) >= 1
    merge = await limited.post(
        f"{base}/items:merge", headers=h, json={"sources": ["WEB-1"], "target": "WEB-2"}
    )
    assert merge.status_code == 429
    # another credential has its own bucket
    ok = await limited.post(f"{base}/commands", headers={"X-Debug-Actor": "@sara"}, json={"commands": [{**_cmd("s"), "actor": "@sara"}]})  # fmt: skip
    assert ok.status_code in (200, 207)


async def test_a_batch_with_too_many_commands_is_a_422_that_points_at_commands(
    limited: httpx.AsyncClient,
) -> None:
    h = {"X-Debug-Actor": "@mahmoud"}
    r = await limited.post("/api/v1/w/acme/capture/batches", headers=h, json={**batch(), "commands": [_cmd(f"t{i}") for i in range(51)]})  # fmt: skip
    assert r.status_code == 422
    err = r.json()["error"]
    assert (
        err["code"] == "schema_violation"
        and err["pointer"] == "/commands"
        and "POST /commands" in err["message"]
    )


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


async def test_html_shells_get_a_nonce_csp_without_remote_origins(api: Api, bundle: Path) -> None:
    await api.call("POST", "/presentations", json={"title": "D", "slides": [{"kind": "kpis"}]})
    pid = (await api.call("GET", "/presentations")).json()["data"][0]["id"]
    r = await api.c.get(f"/p/acme/{pid}", headers={"X-Debug-Actor": "@mahmoud"})
    assert r.status_code == 200, r.text
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


async def test_each_shell_response_gets_a_fresh_nonce(api: Api, bundle: Path) -> None:
    await api.call("POST", "/presentations", json={"title": "D", "slides": [{"kind": "kpis"}]})
    pid = (await api.call("GET", "/presentations")).json()["data"][0]["id"]
    h = {"X-Debug-Actor": "@mahmoud"}
    a = await api.c.get(f"/p/acme/{pid}", headers=h)
    b = await api.c.get(f"/p/acme/{pid}", headers=h)
    assert a.status_code == 200 and b.status_code == 200
    assert a.headers["content-security-policy"] != b.headers["content-security-policy"]


async def test_share_link_shell_cannot_be_framed(api: Api, bundle: Path) -> None:
    r = await api.c.get("/p/t/not-a-token")
    assert r.status_code == 200, r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_bundle_has_only_tag_script_openers(real_bundle: Path) -> None:
    import re

    html = real_bundle.read_text()
    assert "<script" in html
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
    base = dict(
        database_url=migrated_url,
        log_json=False,
        log_level="WARNING",
        session_secret="x" * 40,
        secret_key="k" * 40,
    )
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


def test_sliding_window_counts_n_hits_at_once() -> None:
    t = [0.0]
    w = SlidingWindow(5, 10.0, clock=lambda: t[0])
    assert w.hit("a", 3) is None
    assert w.hit("a", 3) == pytest.approx(10.0)  # 3 + 3 > 5: refused, nothing recorded
    assert w.hit("a", 2) is None
    t[0] = 10.0
    assert w.hit("a", 5) is None


async def test_sign_in_bucket_is_per_address_and_username(limited: httpx.AsyncClient) -> None:
    """13 M4: one user's failed attempts do not lock a colleague behind the same address out."""

    async def go(user: str) -> int:
        r = await limited.post("/auth/ldap/login", json={"workspace": "acme", "username": user, "password": "p"})
        return r.status_code

    assert [await go("alice") for _ in range(3)] == [404, 404, 429]
    assert await go("bob") == 404  # own bucket
    assert await go("ALICE") == 429  # case-insensitive: same person
