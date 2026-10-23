"""HTTP client for the Team Tracker server (bearer token, optional PEM CA bundle)."""

from __future__ import annotations

from typing import Any

import httpx

from tt_client.config import ClientConfig


class ApiError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status, self.body = status, body


class Unreachable(Exception):
    """Network-level failure: the caller should queue and retry later."""


class TTApi:
    def __init__(self, cfg: ClientConfig, *, client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.http = client or httpx.Client(
            base_url=cfg.server_url.rstrip("/"),
            verify=cfg.ca_bundle or True,
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"Authorization": f"Bearer {cfg.token}"} if cfg.token else {},
        )

    def _url(self, path: str) -> str:
        return f"/api/v1/w/{self.cfg.workspace}{path}"

    def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return self.http.request(method, self._url(path), **kw)
        except httpx.TransportError as exc:
            raise Unreachable(str(exc)) from exc

    def _json(self, resp: httpx.Response, ok: tuple[int, ...] = (200, 201)) -> Any:
        if resp.status_code not in ok:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise ApiError(resp.status_code, body)
        return resp.json()

    # -- pack
    def context_pack(self, etag: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        headers = {"If-None-Match": etag} if etag else {}
        r = self._req("GET", "/context-pack", headers=headers)
        if r.status_code == 304:
            return None, etag
        return self._json(r), r.headers.get("etag")

    # -- capture
    def post_batch(self, batch: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Returns (status, body); 200/207 are accepted, anything else is left for the caller to inspect."""
        r = self._req("POST", "/capture/batches", json=batch)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"error": {"code": "bad_response", "message": r.text[:200]}}

    def post_feedback(self, items: list[dict[str, Any]]) -> None:
        self._json(self._req("POST", "/capture/feedback", json=items))

    # -- reads
    def active_activities(self) -> list[dict[str, Any]]:
        data = self._json(self._req("GET", "/activities", params={"status": "active", "person": "me"}))
        return list(data["data"])

    def daily_logs(self, day: str | None = None) -> list[dict[str, Any]]:
        data = self._json(self._req("GET", "/daily-logs", params={"day": day} if day else None))
        return list(data["data"])

    def confirm_daily_log(self, log_id: str) -> dict[str, Any]:
        return dict(self._json(self._req("POST", f"/daily-logs/{log_id}/confirm")))

    # -- auth bootstrap
    def mint_token(self, name: str = "cli") -> str:
        try:
            r = self.http.post("/auth/tokens", json={"workspace": self.cfg.workspace, "name": name})
        except httpx.TransportError as exc:
            raise Unreachable(str(exc)) from exc
        return str(self._json(r)["token"])
