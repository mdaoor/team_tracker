"""Builds the configured calendar providers from `[calendar]` in config.toml and the environment (client ids only; tokens never come from here)."""

from __future__ import annotations

import os
import webbrowser
from collections.abc import Callable
from typing import Any

import httpx

from tt_client.integrations import google as g
from tt_client.integrations import microsoft as ms
from tt_client.integrations.base import AuthRequired, CalendarProvider
from tt_client.integrations.oauth import Authorizer
from tt_client.secrets import SecretStore

DEFAULT_PORT = 8765


def redirect_uri(cfg: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{int(cfg.get('redirect_port', DEFAULT_PORT))}/callback"


def build_providers(
    cfg: dict[str, Any],
    secrets: SecretStore,
    *,
    http: httpx.Client | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, CalendarProvider]:
    """Providers for which a client id is configured. Connecting needs one; syncing needs a stored token."""
    client = http or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
    out: dict[str, CalendarProvider] = {}
    gid = os.environ.get("TT_GOOGLE_CLIENT_ID") or cfg.get("google_client_id")
    if gid:
        out["google"] = g.GoogleCalendar(
            client,
            secrets,
            g.oauth_config(
                str(gid),
                os.environ.get("TT_GOOGLE_CLIENT_SECRET") or cfg.get("google_client_secret"),
                redirect_uri(cfg),
            ),
            clock,
        )
    mid = os.environ.get("TT_MS_CLIENT_ID") or cfg.get("microsoft_client_id")
    if mid:
        out["microsoft"] = ms.MicrosoftCalendar(
            client,
            secrets,
            ms.oauth_config(
                str(mid),
                tenant=str(os.environ.get("TT_MS_TENANT") or cfg.get("microsoft_tenant") or "common"),
                attendance=str(cfg.get("microsoft_attendance", "")).lower() in ("1", "true", "yes"),
                redirect_uri=redirect_uri(cfg),
            ),
            clock,
        )
    return out


def connect(provider: CalendarProvider, open_browser: Callable[[str], Any] = webbrowser.open) -> None:
    """Interactive PKCE sign-in; the token goes to the secret store through the provider's vault."""
    auth: Authorizer | None = getattr(provider, "auth", None)
    if auth is None:
        raise AuthRequired("this provider cannot sign in interactively")
    auth.connect(open_browser)


def disconnect(provider: CalendarProvider) -> None:
    vault = getattr(provider, "vault", None)
    if vault is not None:
        vault.delete()
