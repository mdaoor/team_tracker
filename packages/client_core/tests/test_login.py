"""`tt login` helpers: directory password exchange and the loopback browser hand-off."""

from __future__ import annotations

import urllib.parse
import urllib.request

import httpx
import pytest
from tt_client.login import LoginError, browser_login, check_server, ldap_token
from tt_client.tls import verify_context


def _transport(status: int, body: dict[str, object]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(status, json=body))


def test_ldap_token_returns_token_and_handle_and_surfaces_errors() -> None:
    ok = _transport(201, {"token": "tt_abc", "handle": "@jdoe"})
    assert ldap_token("https://t.example", "acme", "CORP\\jdoe", "pw", transport=ok) == ("tt_abc", "@jdoe")
    bad = _transport(
        401, {"error": {"code": "invalid_credentials", "message": "invalid username or password"}}
    )
    with pytest.raises(LoginError, match="invalid username or password"):
        ldap_token("https://t.example", "acme", "jdoe", "nope", transport=bad)
    with pytest.raises(LoginError):
        ldap_token(
            "https://t.example",
            "acme",
            "jdoe",
            "x",
            transport=httpx.MockTransport(lambda r: httpx.Response(502, text="bad gateway")),
        )


def test_check_server_paths() -> None:
    check_server("http://127.0.0.1:8000", transport=_transport(200, {"status": "ok"}))
    with pytest.raises(LoginError, match="/healthz"):
        check_server("http://localhost:8000", transport=_transport(404, {}))
    with pytest.raises(LoginError, match="not a valid server URL"):
        check_server("ftp://x")

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    with pytest.raises(LoginError, match="cannot reach"):
        check_server("http://localhost:1", transport=httpx.MockTransport(boom))


def test_browser_login_receives_the_token_on_loopback_and_checks_state() -> None:
    seen: dict[str, str] = {}

    def fake_browser(url: str) -> None:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        seen.update({k: v[0] for k, v in q.items()})
        cb = q["callback"][0]
        assert cb.startswith("http://127.0.0.1:")
        wrong = f"{cb}?{urllib.parse.urlencode({'token': 'evil', 'state': 'forged'})}"
        with pytest.raises(urllib.error.HTTPError):  # a forged state is refused and does not end the wait
            urllib.request.urlopen(wrong)  # noqa: S310
        right = (
            f"{cb}?{urllib.parse.urlencode({'token': 'tt_real', 'state': q['state'][0], 'handle': '@sara'})}"
        )
        urllib.request.urlopen(right).read()  # noqa: S310

    token, handle = browser_login("https://t.example/", "acme", open_url=fake_browser, timeout=5)
    assert (token, handle) == ("tt_real", "@sara") and seen["workspace"] == "acme"


def test_browser_login_times_out() -> None:
    with pytest.raises(LoginError, match="timed out"):
        browser_login("https://t.example", "acme", open_url=lambda u: None, timeout=0.2)


def test_verify_context_adds_a_ca_and_rejects_missing_files(tmp_path: object) -> None:
    assert verify_context(None).verify_mode.name == "CERT_REQUIRED"
    with pytest.raises(FileNotFoundError):
        verify_context("/nonexistent.pem")
