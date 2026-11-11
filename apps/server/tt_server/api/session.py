"""Signed session cookies and double-submit CSRF for the web console (Phase 4 §4.5).

The cookie carries only the person id; workspace membership is re-checked on every request. API tokens
(bearer) never use cookies and are exempt from CSRF.
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from tt_server.api.errors import ApiError
from tt_server.settings import Settings

SESSION_COOKIE = "tt_session"
CSRF_COOKIE = "tt_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SLIDE_AFTER_SECONDS = 600  # re-issue the cookie when older than this, so activity keeps the 12 h window open


def _serializer(settings: Settings, salt: str = "tt-session") -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=salt)


def read_session(request: Request, settings: Settings) -> tuple[str, int] | None:
    """(person_id, age in seconds) for a valid, unexpired cookie."""
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        data, issued = _serializer(settings).loads(
            raw, max_age=settings.session_ttl_hours * 3600, return_timestamp=True
        )
    except BadSignature:
        return None
    import time

    return str(data["p"]), int(time.time() - issued.timestamp())


def set_session(
    response: Response, settings: Settings, person_id: str, *, secure: bool, csrf: str | None = None
) -> str:
    token = csrf or secrets.token_urlsafe(24)
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        _serializer(settings).dumps({"p": person_id}),
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(  # readable by the SPA, which echoes it in X-CSRF-Token
        CSRF_COOKIE, token, max_age=max_age, httponly=False, secure=secure, samesite="lax", path="/"
    )
    return token


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def check_csrf(request: Request) -> None:
    """Double-submit: the header must equal the (non-HttpOnly) cookie. Cookie-authenticated writes only."""
    if request.method in SAFE_METHODS:
        return
    cookie, header = request.cookies.get(CSRF_COOKIE, ""), request.headers.get(CSRF_HEADER, "")
    if not cookie or not hmac.compare_digest(cookie, header):
        raise ApiError(403, "csrf_failed", "missing or invalid CSRF token")


def signed_state(settings: Settings, payload: dict[str, str]) -> str:
    return _serializer(settings, "tt-oidc-state").dumps(payload)


def load_state(settings: Settings, raw: str, max_age: int = 600) -> dict[str, str] | None:
    try:
        data = _serializer(settings, "tt-oidc-state").loads(raw, max_age=max_age)
    except BadSignature:
        return None
    return dict(data)
