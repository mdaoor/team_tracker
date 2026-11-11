"""Routing, privacy modes, budgets, structured output (Phase 2b §2b.1, §2b.6)."""

from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from tt_llm.config import GatewayConfig, default_config
from tt_llm.routing import (
    BadModelOutput,
    BudgetExceeded,
    ModelsFailed,
    NoModelAvailable,
    Router,
    litellm_backend,
)
from tt_llm.storage import MemoryStorage

from gateway_tests.conftest import Backend, Harness, request_body, text


def router(cfg: GatewayConfig, backend: Backend) -> Router:
    return Router(cfg, MemoryStorage(), backend)


def test_alias_candidates_follow_privacy_mode() -> None:
    agent = default_config(privacy_mode="cloud").aliases["agent"].models
    assert Router(default_config(privacy_mode="cloud"), MemoryStorage()).candidates("agent") == agent
    local = Router(default_config(privacy_mode="local_only"), MemoryStorage())
    assert local.candidates("agent") == ["ollama/qwen2.5:14b-instruct"]
    assert local.candidates("summariser") == ["ollama/qwen2.5:14b-instruct"]
    with pytest.raises(NoModelAvailable):
        local.candidates("reasoner")  # cloud-only alias
    hybrid = Router(default_config(privacy_mode="hybrid"), MemoryStorage())
    assert (
        hybrid.candidates("summariser")[0] == "anthropic/claude-sonnet-5-5"
    )  # summariser may leave the host
    assert hybrid.candidates("agent") == ["ollama/qwen2.5:14b-instruct"]  # agent may not
    with pytest.raises(NoModelAvailable):
        Router(default_config(), MemoryStorage()).candidates("nope")


def test_a_request_can_tighten_privacy_but_never_loosen_it() -> None:
    r = Router(default_config(privacy_mode="local_only"), MemoryStorage())
    assert r.candidates("agent", "cloud") == ["ollama/qwen2.5:14b-instruct"]
    cloud = Router(default_config(privacy_mode="cloud"), MemoryStorage())
    assert cloud.candidates("agent", "local_only") == ["ollama/qwen2.5:14b-instruct"]


def test_local_only_requires_a_loopback_model_host() -> None:
    with pytest.raises(ValueError, match="localhost"):
        default_config(privacy_mode="local_only", ollama_api_base="http://gpu-box.corp:11434")
    default_config(privacy_mode="local_only", ollama_api_base="http://127.0.0.1:11434")


async def test_fallback_order_and_error_collection() -> None:
    b = Backend([RuntimeError("primary down"), RuntimeError("primary down again"), text({"ok": 1})])
    r = router(default_config(privacy_mode="cloud"), b)
    comp = await r.complete("agent", [{"role": "user", "content": "x"}], workspace="w")
    assert [c["model"] for c in b.calls] == [
        "ollama/qwen2.5:14b-instruct",
        "ollama/qwen2.5:14b-instruct",  # one retry on the primary
        "anthropic/claude-sonnet-5-5",
    ]
    assert comp.content == '{"ok": 1}'
    dead = router(default_config(privacy_mode="cloud"), Backend([RuntimeError("a")] * 4))
    with pytest.raises(ModelsFailed) as exc:
        await dead.complete("agent", [{"role": "user", "content": "x"}], workspace="w")
    assert len(exc.value.extra["errors"]) == 4


async def test_structured_output_gets_one_repair_retry() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    b = Backend([text("not json at all"), text('```json\n{"n": 3}\n```')])
    data, _ = await router(default_config(privacy_mode="cloud"), b).structured(
        "translator", [{"role": "user", "content": "x"}], schema, workspace="w"
    )
    assert data == {"n": 3} and len(b.calls) == 2
    assert (
        b.calls[1]["messages"][-1]["role"] == "user"
        and "Invalid for the schema" in b.calls[1]["messages"][-1]["content"]
    )
    assert b.calls[0]["response_format"]["json_schema"]["schema"] == schema
    bad = Backend([text("nope"), text('{"n": "three"}')])
    with pytest.raises(BadModelOutput):
        await router(default_config(privacy_mode="cloud"), bad).structured(
            "translator", [{"role": "user", "content": "x"}], schema, workspace="w"
        )


async def test_budget_is_enforced_per_workspace_per_day() -> None:
    cfg = default_config(
        privacy_mode="cloud", budgets={"default_usd_per_workspace_per_day": 0.01, "workspaces": {"vip": 5}}
    )
    from tt_llm.routing import Completion

    b = Backend(default=lambda kw: Completion(content="{}", model=kw["model"], cost_usd=0.02))
    r = router(cfg, b)
    await r.complete("agent", [{"role": "user", "content": "x"}], workspace="small")  # spends 0.02 > 0.01
    with pytest.raises(BudgetExceeded) as exc:
        await r.complete("agent", [{"role": "user", "content": "x"}], workspace="small")
    assert 1 <= exc.value.retry_after <= 86400
    await r.complete("agent", [{"role": "user", "content": "x"}], workspace="vip")  # its own, larger limit
    await r.complete("agent", [{"role": "user", "content": "x"}], workspace="vip")


async def test_budget_exceeded_is_http_429_with_retry_after(make: Callable[..., Harness]) -> None:
    from tt_llm.routing import Completion

    b = Backend(default=lambda kw: Completion(content="{}", model=kw["model"], cost_usd=1.0))
    h = make(b, budgets={"default_usd_per_workspace_per_day": 0.5})
    await h.storage.add_spend("acme", datetime.now(UTC).date(), 1.0)
    r = await h.post("/v1/translate", request_body(line="doing fix login"))
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert r.json()["error"]["code"] == "budget_exceeded"
    assert b.calls == []  # refused before any model was called


async def test_local_only_never_opens_a_connection_beyond_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network-deny sandbox: a fake cloud key is present, every non-loopback connect is recorded and refused."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-real")
    attempts: list[Any] = []
    real = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        attempts.append(host)
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError(f"network egress to {host} blocked by the test sandbox")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    cfg = default_config(
        privacy_mode="local_only",
        ollama_api_base="http://127.0.0.1:9",
        limits={"num_retries": 0, "request_timeout_s": 3},
    )
    r = Router(cfg, MemoryStorage(), litellm_backend)
    with pytest.raises(ModelsFailed) as exc:  # nothing listens on port 9: the local model "fails"
        await r.complete("agent", [{"role": "user", "content": "hi"}], workspace="w")
    assert all(
        "anthropic" not in e for e in exc.value.extra["errors"]
    )  # the cloud fallback was never even tried
    # whether the loopback attempt shows up here depends on the event loop and proxy settings; what must hold is that nothing else did
    assert set(attempts) <= {"127.0.0.1", "::1", "localhost"}


class SlowBackend(Backend):
    async def __call__(self, **kw: Any) -> Any:
        import asyncio

        await asyncio.sleep(0.05)  # all callers are in flight together
        return await super().__call__(**kw)


async def test_concurrent_requests_cannot_overspend_at_the_boundary() -> None:
    """Review T14: check-then-charge let N parallel calls all pass the check. With a hold, at most one fits the last slice."""
    import asyncio

    from tt_llm.routing import Completion

    cfg = default_config(
        privacy_mode="cloud",
        budgets={"default_usd_per_workspace_per_day": 0.01, "reserve_usd_per_request": 0.01},
    )
    r = router(
        cfg, SlowBackend(default=lambda kw: Completion(content="{}", model=kw["model"], cost_usd=0.01))
    )
    results = await asyncio.gather(
        *(r.complete("agent", [{"role": "user", "content": "x"}], workspace="acme") for _ in range(6)),
        return_exceptions=True,
    )
    ok = [x for x in results if not isinstance(x, BaseException)]
    refused = [x for x in results if isinstance(x, BudgetExceeded)]
    assert len(ok) == 1 and len(refused) == 5, results


async def test_the_hold_is_released_when_the_model_fails_and_settled_to_the_real_cost() -> None:
    from decimal import Decimal

    from tt_llm.routing import Completion, ModelsFailed

    cfg = default_config(
        privacy_mode="cloud",
        budgets={"default_usd_per_workspace_per_day": 1.0, "reserve_usd_per_request": 0.25},
    )

    def boom(kw: dict[str, Any]) -> Completion:
        raise RuntimeError("provider down")

    r = router(cfg, Backend(default=boom))
    with pytest.raises(ModelsFailed):
        await r.complete("agent", [{"role": "user", "content": "x"}], workspace="acme")
    day = datetime.now(UTC).date()
    assert await r.storage.spent("acme", day) == 0  # nothing leaked

    r2 = router(cfg, Backend(default=lambda kw: Completion(content="{}", model=kw["model"], cost_usd=0.1)))
    await r2.complete("agent", [{"role": "user", "content": "x"}], workspace="acme")
    assert await r2.storage.spent("acme", day) == Decimal("0.1")  # exact: not 0.1000000000000000055


async def test_money_is_decimal_end_to_end() -> None:
    from decimal import Decimal

    store = MemoryStorage()
    day = datetime.now(UTC).date()
    for _ in range(10):
        await store.add_spend("acme", day, 0.1)
    assert await store.spent("acme", day) == Decimal("1.0")  # ten binary 0.1s would sum to 0.9999999999999999
