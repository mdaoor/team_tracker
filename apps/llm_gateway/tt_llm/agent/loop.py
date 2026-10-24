"""The agent loop: small, bounded, auditable (Phase 2b §2b.2)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from opentelemetry import trace

from tt_llm.agent.registry import WRITE_TOOLS, ToolRegistry, truncate
from tt_llm.config import GatewayConfig
from tt_llm.guards import Grounding
from tt_llm.routing import Router
from tt_llm.storage import Run, Step, Storage

log = structlog.get_logger()
tracer = trace.get_tracer("tt_llm")


@dataclass
class AgentResult:
    output: Any
    run: Run
    grounding: Grounding
    tool_calls: int
    warnings: list[str] = field(default_factory=list)


def _safe(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


async def run_agent(
    *,
    router: Router,
    storage: Storage,
    cfg: GatewayConfig,
    run: Run,
    system: str,
    user: str,
    tools: ToolRegistry,
    allowed: list[str],
    output_schema: dict[str, Any],
    grounding: Grounding,
    alias: str = "agent",
    privacy_mode: str | None = None,
) -> AgentResult:
    """Plan -> tool calls -> forced-schema final answer. Write tools run only when the caller listed them."""
    lim = cfg.limits
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    warnings: list[str] = []
    n_calls = 0
    n_step = 0
    await storage.save_run(run)
    try:
        with tracer.start_as_current_span(f"agent.{run.task}") as span:
            for _ in range(lim.max_steps):
                t0 = time.perf_counter()
                comp = await router.complete(
                    alias,
                    messages,
                    workspace=run.workspace,
                    tools=tools.schemas(allowed),
                    privacy_mode=privacy_mode,
                )
                run.model, run.cost_usd = comp.model, run.cost_usd + comp.cost_usd
                n_step += 1
                run.steps.append(
                    Step(
                        n_step,
                        "model",
                        comp.model,
                        {"messages": len(messages)},
                        _safe({"content": comp.content, "tool_calls": [c.name for c in comp.tool_calls]}),
                        int((time.perf_counter() - t0) * 1000),
                    )
                )
                if not comp.tool_calls:
                    if comp.content:
                        messages.append({"role": "assistant", "content": comp.content})
                    break
                messages.append(comp.as_message())
                for call in comp.tool_calls:
                    result: Any
                    t1 = time.perf_counter()
                    n_calls += 1
                    if n_calls > lim.max_tool_calls:
                        result = {"error": "tool_budget_exhausted"}
                        warnings.append("tool call budget exhausted")
                    elif call.name not in allowed or call.name not in tools.tools:
                        result = {"error": "tool_not_allowed", "tool": call.name}
                        warnings.append(f"blocked call to {call.name}")
                        log.warning(
                            "tool_blocked", tool=call.name, write=call.name in WRITE_TOOLS, task=run.task
                        )
                    elif (bad := tools.validate_args(call.name, call.arguments)) is not None:
                        result = {"error": "invalid_arguments", "detail": bad}
                    else:
                        try:
                            result = await tools.call(call.name, call.arguments)
                        except Exception as exc:  # noqa: BLE001 - a failing tool is information for the model, not a crash
                            result = {"error": type(exc).__name__, "detail": str(exc)[:200]}
                    result = truncate(result, lim.tool_result_max_chars)
                    grounding.add_tool_result(call.name, result)
                    n_step += 1
                    run.steps.append(
                        Step(
                            n_step,
                            "tool",
                            call.name,
                            _safe(call.arguments),
                            _safe(result),
                            int((time.perf_counter() - t1) * 1000),
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": json.dumps(result, default=str),
                        }
                    )
            messages.append(
                {"role": "user", "content": "Return the final answer now as JSON that matches the schema."}
            )
            t2 = time.perf_counter()
            output, comp = await router.structured(
                alias, messages, output_schema, workspace=run.workspace, privacy_mode=privacy_mode
            )
            run.model, run.cost_usd = comp.model, run.cost_usd + comp.cost_usd
            n_step += 1
            run.steps.append(
                Step(
                    n_step,
                    "model",
                    comp.model,
                    {"final": True},
                    _safe(output),
                    int((time.perf_counter() - t2) * 1000),
                )
            )
            span.set_attribute("tt.tool_calls", n_calls)
            span.set_attribute("tt.cost_usd", run.cost_usd)
        run.status = "ok"
        return AgentResult(output, run, grounding, n_calls, warnings)
    except Exception:
        run.status = "error"
        raise
    finally:
        from datetime import UTC, datetime

        run.ended_at = datetime.now(UTC)
        await storage.save_run(run)
