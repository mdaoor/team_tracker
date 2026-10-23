"""Live-server fixtures: a real uvicorn on a free port against the migrated test database."""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from tt_server.main import create_app
from tt_server.settings import Settings


@dataclass
class Live:
    url: str
    slug: str
    token: str
    handle: str = "@mahmoud"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def live_server(migrated_url: str) -> Iterator[str]:
    port = _free_port()
    app = create_app(Settings(database_url=migrated_url, log_json=False, log_level="WARNING", dev_auth=True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("live server did not start")
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def live(live_server: str) -> Live:
    """A fresh workspace (unique slug per test) with @mahmoud, @sara, projects WEB/DATA and a token."""
    slug = "e2e-" + uuid.uuid4().hex[:8]
    with httpx.Client(base_url=live_server, timeout=10) as c:
        r = c.post(
            "/api/v1/workspaces",
            json={
                "name": "E2E",
                "slug": slug,
                "owner": {
                    "display_name": "Mahmoud",
                    "email": f"{slug}-m@x.test",
                    "handle": "@mahmoud",
                    "time_zone": "Asia/Riyadh",
                },
            },
        )
        assert r.status_code == 201, r.text
        h = {"X-Debug-Actor": "@mahmoud"}
        base = f"/api/v1/w/{slug}"
        for handle in ("@sara", "@ali"):
            assert (
                c.post(
                    base + "/members",
                    headers=h,
                    json={
                        "display_name": handle[1:],
                        "email": f"{slug}-{handle[1:]}@x.test",
                        "handle": handle,
                    },
                ).status_code
                == 201
            )
        for key, name in (("WEB", "Website"), ("DATA", "Ingestion")):
            assert (
                c.post(
                    base + "/projects",
                    headers=h,
                    json={"key": key, "name": name, "settings": {"aliases": [name.lower()]}},
                ).status_code
                == 201
            )
        tok = c.post("/auth/tokens", headers=h, json={"workspace": slug})
        assert tok.status_code == 201, tok.text
    return Live(live_server, slug, tok.json()["token"])
