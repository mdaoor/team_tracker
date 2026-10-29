from __future__ import annotations

import uuid
from pathlib import Path
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


async def bootstrap_workspace(client: httpx.AsyncClient) -> Api:
    """The standard test workspace: acme, @mahmoud (admin), @sara, @ali, projects DATA and WEB."""
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
    return a


@pytest.fixture
async def api(client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]) -> Api:
    a = await bootstrap_workspace(client)
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        ws.settings = {**ws.settings, "glossary": {"the pipeline": "DATA"}}
        await s.commit()
    return a


@pytest.fixture
async def committed_api(committed_client: httpx.AsyncClient) -> Api:
    """Same workspace over committing sessions: use it when the test fires requests in parallel (asyncio.gather)."""
    return await bootstrap_workspace(committed_client)


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal stand-in for the deck bundle so the HTML shell responds 200 and its headers can be asserted (13 M6)."""
    from tt_server.api.routers import presentations as pres_router

    b = tmp_path / "bundle.html"
    b.write_text("<!doctype html><html><head><title>d</title></head><body></body></html>")
    monkeypatch.setattr(pres_router, "BUNDLE", b)
    return b


@pytest.fixture(scope="session")
def real_bundle() -> Path:
    """The real built deck bundle; built once with `pnpm --filter @tt/presentation build` when dist is missing (CI's python job already builds it)."""
    import subprocess

    from tt_server.api.routers.presentations import BUNDLE

    if not BUNDLE.exists():
        root = BUNDLE.parents[3]
        done = subprocess.run(
            ["pnpm", "--filter", "@tt/presentation", "build"], cwd=root, capture_output=True, text=True
        )
        assert done.returncode == 0 and BUNDLE.exists(), (
            f"cannot build the deck bundle:\n{done.stdout}\n{done.stderr}"
        )
    return BUNDLE
