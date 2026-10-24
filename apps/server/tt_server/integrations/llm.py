"""Client for the LLM gateway (Phase 2b). The core server never talks to a model itself."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from tt_server.settings import Settings


class GatewayUnavailable(Exception):
    """Gateway down, misconfigured or erroring: callers keep working without suggestions."""


class GatewayBudgetExceeded(GatewayUnavailable):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"LLM budget spent; retry in {retry_after}s")
        self.retry_after = retry_after


@dataclass
class GatewayAnswer:
    output: Any
    run_id: str
    model: str | None
    cost_usd: float
    warnings: list[str]
    confidence: float | None = None


class LLMGatewayClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        ca_bundle: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
        self.http = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"} if token else {},
            verify=verify,
            timeout=timeout,
        )

    async def _post(self, path: str, body: dict[str, Any], *, core_token: str | None = None) -> GatewayAnswer:
        headers = {"X-TT-Core-Token": core_token} if core_token else {}
        try:
            r = await self.http.post(path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise GatewayUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise GatewayBudgetExceeded(int(r.headers.get("Retry-After", "3600")))
        if r.status_code >= 400:
            raise GatewayUnavailable(f"gateway answered {r.status_code}")
        d = r.json()
        return GatewayAnswer(
            d.get("output"),
            d["run_id"],
            d.get("model"),
            d.get("cost_usd", 0.0),
            d.get("warnings", []),
            d.get("confidence"),
        )

    async def suggest_review(
        self, workspace: str, row: dict[str, Any], candidates: dict[str, Any]
    ) -> GatewayAnswer:
        return await self._post(
            "/v1/suggest/review", {"workspace": workspace, "row": row, "candidates": candidates}
        )

    async def suggest_classify(
        self, workspace: str, items: list[dict[str, Any]], dimensions: list[dict[str, Any]]
    ) -> GatewayAnswer:
        return await self._post(
            "/v1/suggest/classify", {"workspace": workspace, "items": items, "dimensions": dimensions}
        )

    async def summarise(self, workspace: str, scope: str, facts: dict[str, Any]) -> GatewayAnswer:
        return await self._post(f"/v1/summarise/{scope}", {"workspace": workspace, "facts": facts})

    async def summarise_daily_log(self, workspace: str, facts: dict[str, Any]) -> GatewayAnswer:
        return await self._post("/v1/summarise/daily-log", {"workspace": workspace, "facts": facts})

    async def policy_author(
        self, workspace: str, request: str, core_token: str | None = None
    ) -> GatewayAnswer:
        return await self._post(
            "/v1/policy/author", {"workspace": workspace, "request": request}, core_token=core_token
        )

    async def aclose(self) -> None:
        await self.http.aclose()


def gateway_from_settings(settings: Settings) -> LLMGatewayClient | None:
    if not settings.llm_gateway_url or not settings.features.llm_suggestions:
        return None
    return LLMGatewayClient(
        settings.llm_gateway_url, settings.llm_gateway_token, ca_bundle=settings.llm_gateway_ca_bundle
    )
