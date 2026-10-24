"""Fakes shared by the gateway tests: a scripted model backend and an in-memory core server."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from tt_llm.app import create_app
from tt_llm.config import GatewayConfig, default_config
from tt_llm.core_client import CoreClient
from tt_llm.routing import Completion, ToolCall
from tt_llm.storage import MemoryStorage

TOKEN = "gw-secret"
CORE_TOKEN = "core-secret"

PACK: dict[str, Any] = {
    "context_schema_version": 1,
    "actor": "@mahmoud",
    "workspace": "acme",
    "projects": [
        {"key": "DATA", "name": "Ingestion Pipeline", "aliases": ["ingest", "the pipeline"]},
        {"key": "WEB", "name": "Website Refresh", "aliases": ["site"]},
    ],
    "my_open_items": [
        {"key": "WEB-110", "title": "OAuth v2 token exchange"},
        {"key": "DATA-12", "title": "Retry refactor"},
    ],
    "people": [{"handle": "@sara"}, {"handle": "@mahmoud"}, {"handle": "@ali"}],
    "tags": ["auth"],
    "milestones": [{"name": "Beta", "project": "WEB"}],
    "iterations": [{"name": "Sprint 4", "project": "WEB"}],
    "risks": [{"key": "RSK-1"}],
    "manager_rules": ["prod incident -> priority critical"],
    "glossary": {"the ingest thing": "PROJECT:DATA"},
}


def text(obj: Any) -> Completion:
    return Completion(content=obj if isinstance(obj, str) else json.dumps(obj), model="ollama/fake")


def tools(*calls: tuple[str, dict[str, Any]], content: str | None = None) -> Completion:
    return Completion(
        content=content,
        tool_calls=[ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)],
        model="ollama/fake",
    )


class Backend:
    """Scripted model. `script` items are Completions, exceptions, or callables(kw) -> Completion."""

    def __init__(
        self, script: list[Any] | None = None, default: Callable[[dict[str, Any]], Completion] | None = None
    ) -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kw: Any) -> Completion:
        self.calls.append(kw)
        item = self.script.pop(0) if self.script else (self.default(kw) if self.default else None)
        if item is None:
            raise AssertionError("backend script exhausted")
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(kw)
        item.model = item.model if item.model != "ollama/fake" else kw["model"]
        return item


class FakeCore:
    """Just enough of the core server for the tools; records writes."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.items = [
            {
                "key": "WEB-110",
                "title": "OAuth v2 token exchange",
                "project_key": "WEB",
                "state_category": "doing",
                "score": 0.52,
            },
            {
                "key": "DATA-12",
                "title": "Retry refactor",
                "project_key": "DATA",
                "state_category": "todo",
                "score": 0.1,
            },
        ]
        self.post_status = 200

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path.removeprefix("/api/v1/w/acme")
        if req.method == "POST" and path == "/capture/batches":
            body = json.loads(req.content)
            res = [
                {"client_id": m["client_id"], "status": "applied", "jira": {"status": "deferred"}}
                for m in body["messages"]
            ]
            return httpx.Response(self.post_status, json={"results": res})
        if path == "/context-pack":
            return httpx.Response(200, json=PACK)
        if path == "/items/search":
            q = req.url.params.get("q", "").lower()
            data = [i for i in self.items if any(w in i["title"].lower() for w in q.split())]
            return httpx.Response(200, json={"data": data, "next_cursor": None})
        if path.startswith("/items/"):
            return httpx.Response(
                200, json=next((i for i in self.items if i["key"] == path.rsplit("/", 1)[1]), {})
            )
        if path == "/people":
            return httpx.Response(200, json={"data": [{"handle": p["handle"]} for p in PACK["people"]]})
        if path == "/milestones":
            return httpx.Response(200, json={"data": [{"name": "Beta", "project_key": "WEB"}]})
        if path == "/iterations":
            return httpx.Response(200, json={"data": [{"name": "Sprint 4", "project_key": "WEB"}]})
        if path == "/risks":
            return httpx.Response(200, json={"data": [{"key": "RSK-1", "title": "vendor slips"}]})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def factory(self) -> Callable[[str, str, str | None], CoreClient]:
        def make(workspace: str, token: str, run_id: str | None) -> CoreClient:
            headers = {"Authorization": f"Bearer {token}"}
            if run_id:
                headers["X-Agent-Run-Id"] = run_id
            http = httpx.AsyncClient(
                transport=httpx.MockTransport(self.handler), base_url="http://core", headers=headers
            )
            return CoreClient("http://core", workspace, token, client=http)

        return make


class Harness:
    def __init__(self, cfg: GatewayConfig, backend: Backend, core: FakeCore) -> None:
        self.cfg, self.backend, self.core = cfg, backend, core
        self.storage = MemoryStorage()
        self.app = create_app(cfg, storage=self.storage, backend=backend, core_factory=core.factory())
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://gw",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    async def post(self, path: str, body: dict[str, Any], **headers: str) -> httpx.Response:
        return await self.http.post(path, json=body, headers=headers)


def request_body(**over: Any) -> dict[str, Any]:
    base = {
        "workspace": "acme",
        "actor": "@mahmoud",
        "now": "2026-10-23T12:00:00+00:00",
        "time_zone": "Asia/Riyadh",
        "pack": PACK,
    }
    return {**base, **over}


@pytest.fixture(autouse=True)
def _tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_LLM_GATEWAY_TOKENS", TOKEN)
    monkeypatch.setenv("TT_LLM_ADMIN_TOKEN", "admin-secret")


@pytest.fixture
def core() -> FakeCore:
    return FakeCore()


@pytest.fixture
def make(core: FakeCore) -> Callable[..., Harness]:
    def build(backend: Backend | None = None, cfg: GatewayConfig | None = None, **cfg_over: Any) -> Harness:
        return Harness(
            cfg or default_config(**{"privacy_mode": "cloud", **cfg_over}), backend or Backend(), core
        )

    return build


@pytest.fixture
async def aclose() -> AsyncIterator[list[Harness]]:
    opened: list[Harness] = []
    yield opened
    for h in opened:
        await h.http.aclose()
