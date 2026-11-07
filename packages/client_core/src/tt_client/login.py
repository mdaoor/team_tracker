"""Sign-in helpers for `tt login`: TLS check, directory password login, browser hand-off (Phase 4 §4.6).

There is deliberately no "skip TLS verification" switch: an untrusted chain stops the login, and the fix
is to point `TT_CA_BUNDLE` at the internal CA's PEM.
"""

from __future__ import annotations

import http.server
import secrets
import ssl
import threading
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from tt_client.tls import verify_context

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class LoginError(Exception):
    pass


def _verify(ca_bundle: str | None) -> ssl.SSLContext:
    try:
        return verify_context(ca_bundle)
    except FileNotFoundError as exc:
        raise LoginError(str(exc)) from exc


def check_server(
    url: str, ca_bundle: str | None = None, *, transport: httpx.BaseTransport | None = None
) -> None:
    """Refuse plain HTTP to non-loopback hosts, then prove the TLS chain and that the host speaks our API."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LoginError(f"{url!r} is not a valid server URL")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK:
        raise LoginError("plain HTTP is only allowed for localhost; use an https:// URL")
    try:
        with httpx.Client(verify=_verify(ca_bundle), timeout=5.0, transport=transport) as c:
            r = c.get(url.rstrip("/") + "/healthz")
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "certificate" in str(exc).lower():
            raise LoginError(
                f"TLS verification failed for {parsed.hostname}: {exc}. If the server uses an internal CA, "
                "set TT_CA_BUNDLE (or --ca-bundle) to that CA's PEM file."
            ) from exc
        raise LoginError(f"cannot reach {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise LoginError(f"cannot reach {url}: {exc}") from exc
    if r.status_code != 200:
        raise LoginError(f"{url} did not answer /healthz (HTTP {r.status_code})")


def ldap_token(
    url: str,
    workspace: str,
    username: str,
    password: str,
    ca_bundle: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, str]:
    """Directory password sign-in; returns (api token, @handle) -- the password is never stored."""
    with httpx.Client(verify=_verify(ca_bundle), timeout=15.0, transport=transport) as c:
        r = c.post(
            url.rstrip("/") + "/auth/ldap/token",
            json={"workspace": workspace, "username": username, "password": password},
        )
    if r.status_code != 201:
        try:
            msg = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = r.text[:200]
        raise LoginError(msg)
    body = r.json()
    return str(body["token"]), str(body.get("handle", ""))


def browser_login(
    url: str,
    workspace: str,
    *,
    open_url: Callable[[str], Any],
    timeout: float = 180.0,
) -> tuple[str, str]:
    """Open the server's `/auth/cli` page; receive the token on a one-shot 127.0.0.1 listener."""
    state = secrets.token_urlsafe(16)
    box: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "token" in q:
                box["token"], box["handle"] = q["token"][0], q.get("handle", [""])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Signed in. You can close this tab and return to the terminal.")
                done.set()
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *a: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        callback = f"http://127.0.0.1:{port}/callback"
        q = urllib.parse.urlencode({"workspace": workspace, "callback": callback, "state": state})
        open_url(f"{url.rstrip('/')}/auth/cli?{q}")
        if not done.wait(timeout):
            raise LoginError("timed out waiting for the browser sign-in")
    finally:
        server.shutdown()
        server.server_close()
    return box["token"], box["handle"]
