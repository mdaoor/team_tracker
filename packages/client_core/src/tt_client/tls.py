"""TLS trust for every client (CLI, desktop, Slack bot, gateway): system roots plus an optional CA PEM."""

from __future__ import annotations

import ssl
from pathlib import Path


def verify_context(ca_bundle: str | None = None) -> ssl.SSLContext:
    """Default verification (hostname + chain). `ca_bundle` *adds* the internal CA(s) to the system store."""
    ctx = ssl.create_default_context()
    if ca_bundle:
        if not Path(ca_bundle).is_file():
            raise FileNotFoundError(f"CA bundle {ca_bundle!r} does not exist")
        ctx.load_verify_locations(cafile=ca_bundle)
    return ctx
