"""Gateway -> core server calls (tools), made with the caller's own token."""

from __future__ import annotations

import ssl
from typing import Any

import httpx


class CoreError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"core server answered {status}: {str(body)[:200]}")
        self.status, self.body = status, body


class CoreClient:
    def __init__(
        self,
        base_url: str,
        workspace: str,
        token: str,
        *,
        ca_bundle: str | None = None,
        agent_run_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.workspace = workspace
        headers = {"Authorization": f"Bearer {token}"}
        if agent_run_id:
            headers["X-Agent-Run-Id"] = agent_run_id
        verify: ssl.SSLContext | bool = True
        if ca_bundle:
            verify = ssl.create_default_context(cafile=ca_bundle)
        self.http = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, verify=verify, timeout=10.0
        )

    def _path(self, path: str) -> str:
        return f"/api/v1/w/{self.workspace}{path}"

    async def get(self, path: str, **params: Any) -> Any:
        r = await self.http.get(self._path(path), params={k: v for k, v in params.items() if v is not None})
        return self._ok(r)

    async def post(self, path: str, body: Any) -> Any:
        return self._ok(await self.http.post(self._path(path), json=body))

    @staticmethod
    def _ok(r: httpx.Response) -> Any:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            raise CoreError(r.status_code, body)
        return body

    async def aclose(self) -> None:
        await self.http.aclose()
