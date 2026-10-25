"""Model routing on LiteLLM: aliases, fallbacks, privacy modes, budgets, structured output + repair."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import jsonschema
import structlog

from tt_llm.config import LOCAL_PREFIXES, GatewayConfig
from tt_llm.storage import Storage


def _no_cost_map_download() -> None:
    """LiteLLM fetches its price table from github.com on first import unless told to use the copy it ships. A gateway in
    `local_only` mode promises to open no connection beyond loopback, so it never downloads."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


log = structlog.get_logger()


class GatewayError(Exception):
    status = 500
    code = "gateway_error"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message, self.extra = message, extra


class BudgetExceeded(GatewayError):
    status, code = 429, "budget_exceeded"

    def __init__(self, workspace: str, retry_after: int) -> None:
        super().__init__(f"daily LLM budget for {workspace!r} is spent", retry_after=retry_after)
        self.retry_after = retry_after


class NoModelAvailable(GatewayError):
    status, code = 503, "no_model_available"


class ModelsFailed(GatewayError):
    status, code = 502, "models_failed"


class BadModelOutput(GatewayError):
    status, code = 502, "bad_model_output"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Completion:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in self.tool_calls
            ]
        return msg


# (model, messages, tools, response_format, temperature, timeout, max_tokens, api_base) -> Completion
Backend = Callable[..., Awaitable[Completion]]


async def litellm_backend(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
    temperature: float,
    timeout: float,  # noqa: ASYNC109 - passed through to LiteLLM
    max_tokens: int | None,
    api_base: str | None,
) -> Completion:
    _no_cost_map_download()
    import litellm

    kw: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "timeout": timeout,
    }
    if tools:
        kw["tools"] = tools
    if response_format:
        kw["response_format"] = response_format
    if max_tokens:
        kw["max_tokens"] = max_tokens
    if api_base:
        kw["api_base"] = api_base
    resp = await litellm.acompletion(**kw)
    choice = resp.choices[0].message
    calls: list[ToolCall] = []
    for c in getattr(choice, "tool_calls", None) or []:
        try:
            args = json.loads(c.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {"_unparsable": c.function.arguments}
        calls.append(ToolCall(c.id or f"call_{len(calls)}", c.function.name, args))
    try:
        cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:  # noqa: BLE001 - unknown/local models have no price
        cost = 0.0
    usage = getattr(resp, "usage", None)
    return Completion(
        content=choice.content,
        tool_calls=calls,
        model=model,
        cost_usd=cost,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


def is_local(model: str) -> bool:
    return model.startswith(LOCAL_PREFIXES)


def parse_json(text: str | None) -> Any:
    if text is None:
        raise ValueError("empty model output")
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1)
    return json.loads(t)


async def litellm_embed(*, model: str, texts: list[str], api_base: str | None) -> list[list[float]]:
    _no_cost_map_download()
    import litellm

    kw: dict[str, Any] = {"model": model, "input": texts}
    if api_base:
        kw["api_base"] = api_base
    resp = await litellm.aembedding(**kw)
    return [list(d["embedding"]) for d in resp.data]


class Router:
    def __init__(
        self,
        cfg: GatewayConfig,
        storage: Storage,
        backend: Backend = litellm_backend,
        embed_backend: Callable[..., Awaitable[list[list[float]]]] = litellm_embed,
    ) -> None:
        self.cfg, self.storage, self.backend, self.embed_backend = cfg, storage, backend, embed_backend

    async def embed(
        self, texts: list[str], *, workspace: str, alias: str = "embedder"
    ) -> tuple[list[list[float]], str]:
        await self.check_budget(workspace)
        errors: list[str] = []
        for model in self.candidates(alias):
            try:
                vecs = await self.embed_backend(
                    model=model,
                    texts=texts,
                    api_base=self.cfg.ollama_api_base if model.startswith("ollama") else None,
                )
                return vecs, model
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model}: {type(exc).__name__}")
        raise ModelsFailed("every embedding model failed", errors=errors)

    # -- model selection -------------------------------------------------------------------------
    def candidates(self, alias: str, privacy_mode: str | None = None) -> list[str]:
        spec = self.cfg.aliases.get(alias)
        if spec is None:
            raise NoModelAvailable(f"unknown model alias {alias!r}")
        mode = privacy_mode or self.cfg.privacy_mode
        if privacy_mode and _strictness(privacy_mode) < _strictness(self.cfg.privacy_mode):
            mode = self.cfg.privacy_mode  # a request may tighten privacy, never loosen it
        models = spec.models
        if mode == "local_only" or (mode == "hybrid" and alias not in self.cfg.cloud_aliases):
            models = [x for x in models if is_local(x)]
        if not models:
            raise NoModelAvailable(f"alias {alias!r} has no model allowed under privacy_mode={mode}")
        return models

    # -- budgets ---------------------------------------------------------------------------------
    def limit_for(self, workspace: str) -> Decimal:
        b = self.cfg.budgets
        return Decimal(str(b.workspaces.get(workspace, b.default_usd_per_workspace_per_day)))

    def _retry_after(self, now: datetime) -> int:
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        return max(1, int((midnight - now).total_seconds()))

    async def check_budget(self, workspace: str) -> None:
        """Refuse a workspace whose day is already spent (no hold: used by calls that cost nothing measurable)."""
        now = datetime.now(UTC)
        if await self.storage.spent(workspace, now.date()) >= self.limit_for(workspace):
            raise BudgetExceeded(workspace, self._retry_after(now))

    async def _reserve(self, workspace: str) -> Decimal:
        """Hold the per-request reserve against today's budget, or raise BudgetExceeded. Returns what was held."""
        hold = Decimal(str(self.cfg.budgets.reserve_usd_per_request))
        now = datetime.now(UTC)
        if hold <= 0:
            await self.check_budget(workspace)
            return Decimal(0)
        if not await self.storage.reserve(workspace, now.date(), hold, self.limit_for(workspace)):
            raise BudgetExceeded(workspace, self._retry_after(now))
        return hold

    async def _settle(self, workspace: str, held: Decimal, actual_usd: float | Decimal) -> None:
        """Replace the hold by what the call really cost (negative when it cost less)."""
        delta = Decimal(str(actual_usd)) - held
        if delta:
            await self.storage.add_spend(workspace, datetime.now(UTC).date(), delta)

    async def _charge(self, workspace: str, usd: float) -> None:
        if usd:
            await self.storage.add_spend(workspace, datetime.now(UTC).date(), Decimal(str(usd)))

    # -- completion ------------------------------------------------------------------------------
    async def complete(
        self,
        alias: str,
        messages: list[dict[str, Any]],
        *,
        workspace: str,
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        timeout: float | None = None,  # noqa: ASYNC109 - per-call override, enforced with wait_for
        privacy_mode: str | None = None,
    ) -> Completion:
        held = await self._reserve(workspace)
        try:
            return await self._complete_held(
                alias, messages, workspace, held, tools, schema, temperature, timeout, privacy_mode
            )
        except BaseException:
            await self._settle(workspace, held, 0)  # nothing was spent: give the hold back
            raise

    async def _complete_held(
        self,
        alias: str,
        messages: list[dict[str, Any]],
        workspace: str,
        held: Decimal,
        tools: list[dict[str, Any]] | None,
        schema: dict[str, Any] | None,
        temperature: float,
        timeout: float | None,  # noqa: ASYNC109
        privacy_mode: str | None,
    ) -> Completion:
        lim = self.cfg.limits
        fmt = (
            {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": False}}
            if schema
            else None
        )
        errors: list[str] = []
        for model in self.candidates(alias, privacy_mode):
            for attempt in range(lim.num_retries + 1):
                try:
                    comp = await asyncio.wait_for(
                        self.backend(
                            model=model,
                            messages=messages,
                            tools=tools,
                            response_format=fmt,
                            temperature=temperature,
                            timeout=timeout or lim.request_timeout_s,
                            max_tokens=lim.max_output_tokens,
                            api_base=self.cfg.ollama_api_base if model.startswith("ollama") else None,
                        ),
                        timeout=timeout or lim.request_timeout_s,
                    )
                    await self._settle(workspace, held, comp.cost_usd)
                    held = Decimal(0)  # settled: an exception from here on must not refund it twice
                    return comp
                except GatewayError:
                    raise
                except Exception as exc:  # noqa: BLE001 - any provider failure moves on to the next attempt/model
                    errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
                    log.warning("model_failed", model=model, attempt=attempt, error=type(exc).__name__)
        raise ModelsFailed("every model for this alias failed", errors=errors)

    async def structured(
        self,
        alias: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        workspace: str,
        temperature: float = 0.0,
        timeout: float | None = None,  # noqa: ASYNC109 - per-call override, enforced with wait_for
        privacy_mode: str | None = None,
    ) -> tuple[Any, Completion]:
        """JSON constrained by `schema`; one repair retry when the first answer does not validate."""
        comp = await self.complete(
            alias,
            messages,
            workspace=workspace,
            schema=schema,
            temperature=temperature,
            timeout=timeout,
            privacy_mode=privacy_mode,
        )
        try:
            return _validate(comp.content, schema), comp
        except (ValueError, jsonschema.ValidationError) as exc:
            repair = [
                *messages,
                {"role": "assistant", "content": comp.content},
                {
                    "role": "user",
                    "content": f"Invalid for the schema ({str(exc)[:300]}). Reply with corrected JSON only.",
                },
            ]
            second = await self.complete(
                alias,
                repair,
                workspace=workspace,
                schema=schema,
                temperature=temperature,
                timeout=timeout,
                privacy_mode=privacy_mode,
            )
            second.cost_usd += comp.cost_usd
            try:
                return _validate(second.content, schema), second
            except (ValueError, jsonschema.ValidationError) as exc2:
                raise BadModelOutput(
                    "model output does not match the schema after one repair", detail=str(exc2)[:300]
                ) from exc2


def _validate(text: str | None, schema: dict[str, Any]) -> Any:
    data = parse_json(text)
    jsonschema.validate(data, schema)
    return data


def _strictness(mode: str) -> int:
    return {"cloud": 0, "hybrid": 1, "local_only": 2}[mode]
