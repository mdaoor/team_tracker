"""FastAPI application: the task endpoints (Phase 2b "Scope" 3)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import structlog
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from tt_llm.auth import Admin, Caller
from tt_llm.config import GatewayConfig, load_config
from tt_llm.core_client import CoreClient
from tt_llm.routing import Backend, GatewayError, Router, litellm_backend
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.storage import MemoryStorage, Storage
from tt_llm.tasks import batch_review as t_batch
from tt_llm.tasks import capture as t_capture
from tt_llm.tasks import embed as t_embed
from tt_llm.tasks import policy as t_policy
from tt_llm.tasks import rules as t_rules
from tt_llm.tasks import suggest as t_suggest
from tt_llm.tasks import summarise as t_sum
from tt_llm.tasks import translate as t_translate
from tt_llm.tasks.base import CoreFactory, Deps
from tt_llm.telemetry import COST, LATENCY, REQUESTS

log = structlog.get_logger()
CoreToken = Annotated[str | None, Header(alias="X-TT-Core-Token")]


def default_core_factory(cfg: GatewayConfig) -> CoreFactory:
    def make(workspace: str, token: str, run_id: str | None) -> CoreClient | None:
        if not workspace:
            return None
        return CoreClient(
            cfg.server.base_url, workspace, token, ca_bundle=cfg.server.ca_bundle, agent_run_id=run_id
        )

    return make


def create_app(
    cfg: GatewayConfig | None = None,
    *,
    storage: Storage | None = None,
    backend: Backend = litellm_backend,
    core_factory: CoreFactory | None = None,
    embed_backend: Any = None,
) -> FastAPI:
    cfg = cfg or load_config()
    storage = storage or MemoryStorage()
    kw = {"embed_backend": embed_backend} if embed_backend else {}
    router = Router(cfg, storage, backend, **kw)
    deps = Deps(cfg, router, storage, core_factory or default_core_factory(cfg))
    app = FastAPI(title="Team Tracker LLM gateway", version="0.1.0")
    app.state.cfg, app.state.deps = cfg, deps

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        headers = {"Retry-After": str(exc.extra["retry_after"])} if "retry_after" in exc.extra else {}
        body = {"error": {"code": exc.code, "message": exc.message, **{k: v for k, v in exc.extra.items()}}}
        return JSONResponse(body, status_code=exc.status, headers=headers)

    async def timed(task: str, req: TaskRequest, call: Callable[[], Awaitable[TaskResponse]]) -> TaskResponse:
        t0 = time.perf_counter()
        outcome = "ok"
        try:
            resp = await call()
            COST.add(resp.cost_usd, {"task": task, "workspace": req.workspace or "-"})
            return resp
        except GatewayError as exc:
            outcome = exc.code
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            REQUESTS.add(1, {"task": task, "outcome": outcome})
            LATENCY.record((time.perf_counter() - t0) * 1000, {"task": task})

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict[str, Any]:
        return {"status": "ready", "privacy_mode": cfg.privacy_mode, "aliases": sorted(cfg.aliases)}

    @app.post("/v1/translate", dependencies=[Caller], tags=["tasks"])
    async def translate(req: TaskRequest) -> TaskResponse:
        return await timed("translate", req, lambda: t_translate.translate(deps, req))

    @app.post("/v1/agent/capture", dependencies=[Caller], tags=["tasks"])
    async def agent_capture(req: TaskRequest, core_token: CoreToken = None) -> TaskResponse:
        return await timed("agent_capture", req, lambda: t_capture.agent_capture(deps, req, core_token))

    @app.post("/v1/agent/batch-review", dependencies=[Caller], tags=["tasks"])
    async def batch_review(req: TaskRequest) -> TaskResponse:
        return await timed("batch_review", req, lambda: t_batch.batch_review(deps, req))

    @app.post("/v1/summarise/daily-log", dependencies=[Caller], tags=["tasks"])
    async def summarise_daily(req: TaskRequest) -> TaskResponse:
        return await timed("summarise_daily_log", req, lambda: t_sum.summarise_daily_log(deps, req))

    @app.post("/v1/summarise/{scope}", dependencies=[Caller], tags=["tasks"])
    async def summarise(scope: str, req: TaskRequest) -> TaskResponse:
        return await timed(f"summarise_{scope}", req, lambda: t_sum.summarise(deps, req, scope))

    @app.post("/v1/suggest/review", dependencies=[Caller], tags=["tasks"])
    async def suggest_review(req: TaskRequest) -> TaskResponse:
        return await timed("suggest_review", req, lambda: t_suggest.suggest_review(deps, req))

    @app.post("/v1/suggest/classify", dependencies=[Caller], tags=["tasks"])
    async def suggest_classify(req: TaskRequest) -> TaskResponse:
        return await timed("suggest_classify", req, lambda: t_suggest.suggest_classify(deps, req))

    @app.post("/v1/policy/author", dependencies=[Caller], tags=["tasks"])
    async def policy_author(req: TaskRequest, core_token: CoreToken = None) -> TaskResponse:
        return await timed("policy_author", req, lambda: t_policy.policy_author(deps, req, core_token))

    @app.post("/v1/rules/dry-run", dependencies=[Caller], tags=["tasks"])
    async def rules_dry_run(req: TaskRequest) -> TaskResponse:
        return await timed("rules_dry_run", req, lambda: t_rules.rules_dry_run(deps, req))

    @app.post("/v1/embed", dependencies=[Caller], tags=["tasks"])
    async def embed(req: TaskRequest) -> TaskResponse:
        return await timed("embed", req, lambda: t_embed.embed(deps, req))

    @app.get("/v1/runs/{run_id}", dependencies=[Admin], tags=["admin"])
    async def get_run(run_id: str) -> dict[str, Any]:
        run = await storage.get_run(run_id)
        if run is None:
            return JSONResponse({"error": {"code": "not_found", "message": "run not found"}}, status_code=404)  # type: ignore[return-value]
        return {
            "id": run.id,
            "task": run.task,
            "workspace": run.workspace,
            "actor": run.actor,
            "alias": run.model_alias,
            "model": run.model,
            "status": run.status,
            "cost_usd": run.cost_usd,
            "meta": run.meta,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "steps": [
                {"n": s.n, "kind": s.kind, "name": s.name, "input": s.input, "output": s.output, "ms": s.ms}
                for s in run.steps
            ],
        }

    return app
