"""Best-effort sliding-window rate limiting (phase 12 §5): capture per credential, sign-in endpoints per client address.

In-process on purpose: it protects each API process from a runaway client or a credential-stuffing burst without a new moving part.
The sign-in endpoints also have the database-backed, cluster-wide backoff (`LoginAttempt`); this layer is the cheap first line.
Behind several replicas the effective limit is `limit x replicas`, which is acceptable for abuse control (not for billing)."""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from collections.abc import Callable

from starlette.requests import Request

from tt_server.sso import trusted_peer


class SlidingWindow:
    def __init__(
        self, limit: int, window_s: float = 60.0, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit, self.window, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._swept = clock()

    def hit(self, key: str, n: int = 1) -> float | None:
        """Record `n` requests. Returns None when allowed, else the seconds until enough hits leave the window."""
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) + n > self.limit:
            # the hit that must expire for `n` more to fit; a single call larger than the limit waits a full window
            idx = len(q) + n - self.limit - 1
            return max(0.0, self.window - (now - q[idx])) if idx < len(q) else self.window
        q.extend([now] * n)
        if now - self._swept > self.window * 5:  # forget idle keys so the table cannot grow without bound
            self._swept = now
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                del self._hits[k]
        return None


def credential_key(
    authorization: str | None, session_cookie: str | None, debug_actor: str | None, client: str | None
) -> str:
    """A stable, non-reversible bucket per caller: bearer token > session cookie > debug actor > client address."""
    for tag, raw in (("t", authorization), ("s", session_cookie), ("d", debug_actor), ("a", client)):
        if raw:
            return tag + hashlib.sha256(raw.encode()).hexdigest()[:20]
    return "anon"


def signin_key(client: str | None, username: str | None) -> str:
    """The sign-in bucket: (client address, username) so one person's typos cannot lock colleagues behind the same NAT out;
    requests without a username in the body fall back to the address alone."""
    if not username:
        return credential_key(None, None, None, client)
    return "u" + hashlib.sha256(f"{client or ''}|{username.strip().lower()}".encode()).hexdigest()[:20]


async def signin_username(request: Request) -> str | None:
    """The `username` of a JSON sign-in body, or None (other content types or a malformed body)."""
    if not request.headers.get("content-type", "").startswith("application/json"):
        return None
    try:
        body = json.loads(await request.body())
    except ValueError:
        return None
    user = body.get("username") if isinstance(body, dict) else None
    return user if isinstance(user, str) and user else None


def client_ip(settings: object, peer: str | None, forwarded_for: str | None) -> str | None:
    """The caller's address. `X-Forwarded-For` is believed only when the TCP peer is a configured trusted proxy
    (`TT_TRUSTED_PROXIES`); then the right-most hop that is not itself a trusted proxy is the client. Anything else is
    attacker-controlled and ignored, so a stranger cannot pick their own bucket (Phase 13 R3.4, review T1)."""

    if not peer or not forwarded_for or not trusted_peer(settings, peer):  # type: ignore[arg-type]
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if not trusted_peer(settings, hop):  # type: ignore[arg-type]
            return hop
    return hops[0] if hops else peer
