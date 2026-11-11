"""Transport helpers: the plain-HTTP listener that only redirects (Phase 4 §4.6) and certificate hot-reload (ADR-0036)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

Scope = dict[str, Any]


def redirect_app(https_port: int) -> Any:
    """ASGI app answering every plain-HTTP request with `308 https://<host>:<https_port><path>`."""

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"localhost").decode().split(":")[0]
        port = "" if https_port == 443 else f":{https_port}"
        query = f"?{scope['query_string'].decode()}" if scope.get("query_string") else ""
        location = f"https://{host}{port}{scope['path']}{query}".encode()
        await send(
            {
                "type": "http.response.start",
                "status": 308,
                "headers": [(b"location", location), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    return app


# ---------------------------------------------------------------------------------------------------- certificate hot-reload

log = structlog.get_logger()


def days_until_expiry(certfile: str | Path, now: datetime | None = None) -> float:
    """Days left on the first certificate in a PEM file (negative once expired)."""
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(Path(certfile).read_bytes())
    return ((cert.not_valid_after_utc) - (now or datetime.now(UTC))).total_seconds() / 86400


class CertReloader:
    """Swap the served certificate on a live `ssl.SSLContext` without a restart: on SIGHUP, and whenever either PEM file changes.

    New handshakes use the new chain; connections already open keep theirs. A bad replacement (half-written file, key that does not
    match the certificate) is logged and ignored - the old certificate keeps serving - and retried at the next check."""

    def __init__(
        self,
        ctx: ssl.SSLContext,
        certfile: str | Path,
        keyfile: str | Path,
        *,
        interval: float = 30.0,
        warn_days: int = 14,
        on_reload: Callable[[str], None] | None = None,
    ) -> None:
        self.ctx, self.cert, self.key = ctx, Path(certfile), Path(keyfile)
        self.interval, self.warn_days, self.on_reload = interval, warn_days, on_reload
        self._seen = self._fingerprint()
        self.reloads = 0
        self.last_error: str | None = None

    def _fingerprint(self) -> tuple[int, ...]:
        out: list[int] = []
        for p in (self.cert, self.key):
            try:
                st = p.stat()
                out += [st.st_mtime_ns, st.st_size]
            except OSError:
                out += [-1, -1]
        return tuple(out)

    def reload(self, reason: str) -> bool:
        try:
            # Validate on a scratch context first: OpenSSL installs the certificate before it checks the key, so a failed load
            # straight into the live context would leave the new certificate paired with the old key and break every handshake.
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(str(self.cert), str(self.key))
            self.ctx.load_cert_chain(str(self.cert), str(self.key))
        except (ssl.SSLError, OSError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.error("tls.reload_failed", reason=reason, error=self.last_error)
            return False
        self._seen, self.last_error = self._fingerprint(), None
        self.reloads += 1
        log.info("tls.reloaded", reason=reason, days_left=round(self.days_left(), 1))
        if self.on_reload:
            self.on_reload(reason)
        return True

    def check(self) -> bool:
        """Reload when the files changed since the last successful load (or last attempt for the same bytes)."""
        now = self._fingerprint()
        if now == self._seen:
            self.warn_if_expiring()
            return False
        ok = self.reload("files changed")
        if not ok:
            self._seen = (
                now  # do not spam the log every tick for the same broken bytes; a further change retries
            )
        return ok

    def days_left(self) -> float:
        return days_until_expiry(self.cert)

    def warn_if_expiring(self) -> None:
        try:
            left = self.days_left()
        except (OSError, ValueError):
            return
        if left < self.warn_days:
            log.warning("tls.expiring", days_left=round(left, 1), certfile=str(self.cert))

    async def run(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        with contextlib.suppress(
            NotImplementedError, RuntimeError, ValueError
        ):  # not on Windows / not in the main thread
            loop.add_signal_handler(signal.SIGHUP, lambda: self.reload("SIGHUP"))
        self.warn_if_expiring()
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            if not stop.is_set():
                self.check()
