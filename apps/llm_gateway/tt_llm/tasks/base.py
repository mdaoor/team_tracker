"""Shared plumbing for tasks: dependencies, run bookkeeping, response assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tt_schema.base import new_id

from tt_llm import prompts
from tt_llm.config import GatewayConfig
from tt_llm.core_client import CoreClient
from tt_llm.guards import SYSTEM_GUARD
from tt_llm.routing import Router
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.storage import Run, Storage

CoreFactory = Callable[[str, str, str | None], CoreClient | None]  # (workspace, token, agent_run_id)


@dataclass
class Deps:
    cfg: GatewayConfig
    router: Router
    storage: Storage
    core_factory: CoreFactory
    catalogues: dict[str, tuple[str | None, dict[str, Any]]] = field(
        default_factory=dict
    )  # workspace -> (etag, doc)


def new_run(task: str, req: TaskRequest, alias: str) -> Run:
    return Run(id=new_id(), task=task, workspace=req.workspace or "-", actor=req.actor, model_alias=alias)


async def finish(deps: Deps, run: Run, status: str = "ok") -> None:
    run.status, run.ended_at = status, datetime.now(UTC)
    await deps.storage.save_run(run)


def system_prompt(deps: Deps, task: str, **slots: str) -> str:
    return prompts.load(task, deps.cfg.prompt_versions.get(task, "v1"), **slots) + "\n\n" + SYSTEM_GUARD


def respond(run: Run, **kw: Any) -> TaskResponse:
    return TaskResponse(run_id=run.id, model=run.model, cost_usd=round(run.cost_usd, 6), **kw)
