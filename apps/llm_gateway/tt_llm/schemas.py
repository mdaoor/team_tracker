"""Request / response envelopes (Phase 2b "Data & API contracts") and the model-facing output schemas."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from tt_schema import client_message_json_schema
from tt_schema.base import new_id


class Options(BaseModel):
    send: bool = False  # agentic capture: post the batch to the core server on the caller's behalf
    apply: bool = False  # server-initiated tasks: allowed to write
    model_alias: str | None = None
    privacy_mode: Literal["local_only", "hybrid", "cloud"] | None = (
        None  # may only tighten the server setting
    )


class TaskRequest(BaseModel):
    """One envelope for every task; each task reads the fields it needs.

    `line`/`actor`/`now`/`time_zone`/`pack` are exactly what `tt_client.GatewayTranslator` already sends."""

    workspace: str = ""
    actor: str = "@unknown"
    line: str | None = None
    lines: list[str] = Field(default_factory=list)
    now: datetime = Field(default_factory=lambda: datetime.now(UTC))
    time_zone: str = "UTC"
    pack: dict[str, Any] = Field(default_factory=dict)
    options: Options = Field(default_factory=Options)
    # task-specific payloads
    batches: list[dict[str, Any]] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    row: dict[str, Any] = Field(default_factory=dict)
    candidates: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    request: str | None = None
    rules: list[str] = Field(default_factory=list)
    texts: list[str] = Field(default_factory=list)

    def all_lines(self) -> list[str]:
        return [x for x in ([self.line] if self.line else []) + self.lines if x and x.strip()]


class TaskResponse(BaseModel):
    output: Any = None
    batch: dict[str, Any] | None = None  # translate / agent capture (the shape tt_client reads)
    batches: list[dict[str, Any]] | None = None  # batch review (the shape tt_client reads)
    confidence: float | None = None
    rationale: str | None = None
    run_id: str
    model: str | None = None
    cost_usd: float = 0.0
    warnings: list[str] = Field(default_factory=list)


def model_batch_schema() -> dict[str, Any]:
    """`ClientBatch` schema as the *model* sees it: identity fields are filled in by code."""
    schema: dict[str, Any] = copy.deepcopy(dict(client_message_json_schema(batch=True)))
    schema["required"] = [r for r in schema.get("required", []) if r != "workspace"]
    msgs = schema["properties"]["messages"]["items"]
    msgs["required"] = [r for r in msgs.get("required", []) if r not in {"actor", "at", "client_id"}]
    return schema


def capture_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"batch": model_batch_schema(), "rationale": {"type": "string"}},
        "required": ["batch"],
    }


def fill_identity(batch: dict[str, Any], *, actor: str, now: datetime, workspace: str) -> dict[str, Any]:
    """Deterministic fields the model never decides."""
    batch = dict(batch)
    batch["workspace"] = workspace
    msgs = []
    for m in batch.get("messages", []):
        m = dict(m)
        m["actor"], m["at"] = actor, now.isoformat()
        m.setdefault("client_id", new_id())
        msgs.append(m)
    batch["messages"] = msgs
    return batch
