"""Authorised GET for the calendar adapters: bearer token, one refresh on 401, errors mapped to `ProviderError`s that never carry a token."""

from __future__ import annotations

from typing import Any

import httpx

from tt_client.integrations.base import AuthRequired, ProviderError, RateLimited, SyncTokenExpired
from tt_client.integrations.oauth import Authorizer


class ApiClient:
    def __init__(self, http: httpx.Client, auth: Authorizer) -> None:
        self.http, self.auth = http, auth

    def get(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        for attempt in (1, 2):
            token = self.auth.access_token(force_refresh=attempt == 2)
            try:
                r = self.http.get(
                    url, params=params, headers={"Authorization": f"Bearer {token}", **(headers or {})}
                )
            except httpx.TransportError as exc:
                raise ProviderError(
                    f"network error talking to {httpx.URL(url).host}: {type(exc).__name__}"
                ) from exc
            if r.status_code == 401 and attempt == 1:
                continue
            return self._check(r)
        raise AuthRequired("the provider rejected the token twice: connect the calendar again")

    @staticmethod
    def _check(r: httpx.Response) -> dict[str, Any]:
        if r.status_code == 410:
            raise SyncTokenExpired("sync token expired")
        if r.status_code == 401:
            raise AuthRequired("the provider rejected the token: connect the calendar again")
        if r.status_code == 429 or (r.status_code == 403 and "rateLimit" in r.text):
            raise RateLimited(_retry_after(r))
        if r.status_code >= 400:
            raise ProviderError(f"{httpx.URL(str(r.url)).host} answered HTTP {r.status_code}: {_message(r)}")
        try:
            return dict(r.json())
        except ValueError as exc:
            raise ProviderError("the provider answered with a non-JSON body") from exc


def _retry_after(r: httpx.Response) -> float:
    try:
        return float(r.headers.get("Retry-After", "60"))
    except ValueError:
        return 60.0


def _message(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        return str(err.get("message") or err)[:200] if isinstance(err, dict) else str(err)[:200]
    except (ValueError, AttributeError):
        return r.text[:200]
