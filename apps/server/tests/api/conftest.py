from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m


class Api:
    """Thin helper around the test client: dev-auth as a handle, workspace-scoped URLs."""

    def __init__(self, client: httpx.AsyncClient, slug: str = "acme") -> None:
        self.c, self.slug = client, slug

    def url(self, path: str) -> str:
        return f"/api/v1/w/{self.slug}{path}"

    async def call(self, method: str, path: str, as_: str = "@mahmoud", **kw: Any) -> httpx.Response:
        return await self.c.request(method, self.url(path), headers={"X-Debug-Actor": as_}, **kw)

    async def role(self, handle: str, role: str, **scope: str) -> None:
        r = await self.call("POST", "/role-assignments", json={"handle": handle, "role": role, **scope})
        assert r.status_code == 201, r.text

    async def send(self, as_: str = "@mahmoud", **msg: Any) -> dict[str, Any]:
        body = {
            "workspace": self.slug,
            "messages": [
                {"actor": as_, "at": "2026-10-23T10:00:00+03:00", "client_id": str(uuid.uuid4()), **msg}
            ],
        }
        r = await self.call("POST", "/capture/batches", as_=as_, json=body)
        assert r.status_code == 200, r.text
        return r.json()["results"][0]  # type: ignore[no-any-return]


@pytest.fixture
async def api(client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]) -> Api:
    r = await client.post(
        "/api/v1/workspaces",
        json={
            "name": "Acme",
            "slug": "acme",
            "owner": {
                "display_name": "Mahmoud",
                "email": "mahmoud@acme.test",
                "handle": "@mahmoud",
                "time_zone": "Asia/Riyadh",
            },
        },
    )
    assert r.status_code == 201, r.text
    a = Api(client)
    for handle in ("@sara", "@ali"):
        r = await a.call(
            "POST",
            "/members",
            json={"display_name": handle[1:].title(), "email": f"{handle[1:]}@acme.test", "handle": handle},
        )
        assert r.status_code == 201, r.text
    r = await a.call(
        "POST",
        "/projects",
        json={"name": "Ingestion Pipeline", "key": "DATA", "settings": {"aliases": ["ingest"]}},
    )
    assert r.status_code == 201, r.text
    r = await a.call("POST", "/projects", json={"name": "Website Refresh", "key": "WEB"})
    assert r.status_code == 201, r.text
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        ws.settings = {**ws.settings, "glossary": {"the pipeline": "DATA"}}
        await s.commit()
    return a
