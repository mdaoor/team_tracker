"""OAuth 2.0 authorization-code flow with PKCE over plain httpx (RFC 7636 / RFC 8252 loopback), and token storage in the keyring.

No MSAL, no google-auth: the two providers differ only in URLs and scopes. Tokens are stored by `TokenVault` through
`tt_client.secrets` (OS keyring, or a 0600 file on headless machines); they are never written to the outbox, the config or a log line.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets as _secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from tt_client.integrations.base import AuthRequired, ProviderError
from tt_client.secrets import SecretStore

EXPIRY_MARGIN_S = 60


def make_pkce() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = base64.urlsafe_b64encode(_secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    client_secret: str | None = (
        None  # Google "desktop" clients carry a non-confidential secret; Microsoft public clients none
    )
    extra_auth: dict[str, str] = field(default_factory=dict)


@dataclass
class Token:
    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str = ""

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at - EXPIRY_MARGIN_S

    def dumps(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def loads(cls, raw: str) -> Token:
        d = json.loads(raw)
        return cls(d["access_token"], d.get("refresh_token"), float(d["expires_at"]), d.get("scope", ""))


def authorize_url(cfg: OAuthConfig, *, state: str, challenge: str) -> str:
    params = {
        "client_id": cfg.client_id,
        "response_type": "code",
        "redirect_uri": cfg.redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **cfg.extra_auth,
    }
    return f"{cfg.auth_url}?{urlencode(params)}"


def parse_redirect(url: str, expected_state: str) -> str:
    """The `code` from the redirect, after checking `state` (CSRF) and provider errors."""
    q = parse_qs(urlparse(url).query)
    if "error" in q:
        raise AuthRequired(f"sign-in refused: {q['error'][0]} {q.get('error_description', [''])[0]}".strip())
    if q.get("state", [""])[0] != expected_state:
        raise AuthRequired("sign-in answer did not match the request (state)")
    if "code" not in q:
        raise AuthRequired("sign-in answer had no code")
    return q["code"][0]


def _token_from(body: dict[str, Any], previous_refresh: str | None, now: float) -> Token:
    if "access_token" not in body:
        raise AuthRequired(
            f"token endpoint refused: {body.get('error', 'unknown')} {body.get('error_description', '')}".strip()
        )
    return Token(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token") or previous_refresh,
        expires_at=now + float(body.get("expires_in", 3600)),
        scope=body.get("scope", ""),
    )


def exchange_code(
    http: httpx.Client, cfg: OAuthConfig, code: str, verifier: str, *, now: float | None = None
) -> Token:
    data = {
        "client_id": cfg.client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "code_verifier": verifier,
        **({"client_secret": cfg.client_secret} if cfg.client_secret else {}),
    }
    r = http.post(cfg.token_url, data=data)
    return _token_from(_json(r), None, now if now is not None else time.time())


def refresh_token(http: httpx.Client, cfg: OAuthConfig, token: Token, *, now: float | None = None) -> Token:
    if not token.refresh_token:
        raise AuthRequired("no refresh token: connect the calendar again")
    data = {
        "client_id": cfg.client_id,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
        "scope": " ".join(cfg.scopes),
        **({"client_secret": cfg.client_secret} if cfg.client_secret else {}),
    }
    r = http.post(cfg.token_url, data=data)
    return _token_from(_json(r), token.refresh_token, now if now is not None else time.time())


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError as exc:
        raise ProviderError(f"token endpoint answered {r.status_code} with a non-JSON body") from exc
    if r.status_code >= 500:
        raise ProviderError(f"token endpoint failed: HTTP {r.status_code}")
    return dict(body)


class TokenVault:
    """Tokens for one provider, in the secret store under `oauth:<provider>`."""

    def __init__(self, secrets: SecretStore, provider: str) -> None:
        self.secrets, self.name = secrets, f"oauth:{provider}"

    def load(self) -> Token | None:
        raw = self.secrets.get(self.name)
        return Token.loads(raw) if raw else None

    def save(self, token: Token) -> None:
        self.secrets.set(self.name, token.dumps())

    def delete(self) -> None:
        self.secrets.delete(self.name)


class Authorizer:
    """Gives providers a valid access token, refreshing once it is (nearly) expired."""

    def __init__(
        self, http: httpx.Client, cfg: OAuthConfig, vault: TokenVault, clock: Any = time.time
    ) -> None:
        self.http, self.cfg, self.vault, self.clock = http, cfg, vault, clock

    def access_token(self, *, force_refresh: bool = False) -> str:
        token = self.vault.load()
        if token is None:
            raise AuthRequired("not connected: run `tt cal connect`")
        if force_refresh or token.expired(self.clock()):
            token = refresh_token(self.http, self.cfg, token, now=self.clock())
            self.vault.save(token)
        return token.access_token

    def connect(self, open_browser: Any, *, listen: Any = None, timeout: float = 180.0) -> Token:
        """Interactive: open the consent page, catch the loopback redirect, exchange the code, store the token."""
        verifier, challenge = make_pkce()
        state = _secrets.token_urlsafe(16)
        url = authorize_url(self.cfg, state=state, challenge=challenge)
        waiter = (listen or LoopbackListener(self.cfg.redirect_uri)).start()
        open_browser(url)
        redirect = waiter.wait(timeout)
        token = exchange_code(
            self.http, self.cfg, parse_redirect(redirect, state), verifier, now=self.clock()
        )
        self.vault.save(token)
        return token


class LoopbackListener:
    """A one-shot HTTP server on 127.0.0.1 that records the OAuth redirect (RFC 8252 section 7.3)."""

    def __init__(self, redirect_uri: str) -> None:
        u = urlparse(redirect_uri)
        if u.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ProviderError("the redirect must be a loopback address")
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 80
        self._url: str | None = None
        self._done = threading.Event()
        self._server: HTTPServer | None = None

    def start(self) -> LoopbackListener:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                outer._url = f"http://{outer.host}:{outer.port}{self.path}"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Team Tracker is connected. You can close this tab.")
                outer._done.set()

            def log_message(self, *args: object) -> None:  # silence: never log URLs that carry codes
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def wait(self, timeout: float) -> str:
        try:
            if not self._done.wait(timeout) or self._url is None:
                raise AuthRequired("timed out waiting for the browser sign-in")
            return self._url
        finally:
            if self._server:
                self._server.shutdown()
                self._server.server_close()
