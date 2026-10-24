"""Gateway configuration: `llm_gateway.yaml` + `TT_LLM_*` environment overrides (Phase 2b §2b.1)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, model_validator

PrivacyMode = Literal["local_only", "hybrid", "cloud"]
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
LOCAL_PREFIXES = ("ollama/", "ollama_chat/", "llamacpp/", "llama.cpp/")


class AliasCfg(BaseModel):
    primary: str
    fallbacks: list[str] = Field(default_factory=list)

    @property
    def models(self) -> list[str]:
        return [self.primary, *self.fallbacks]


class Limits(BaseModel):
    max_steps: int = 8
    max_tool_calls: int = 12
    max_output_tokens: int = 2048
    request_timeout_s: float = 20.0
    translate_timeout_s: float = 1.5
    num_retries: int = 1
    tool_result_max_chars: int = 6000


class BudgetCfg(BaseModel):
    default_usd_per_workspace_per_day: float = 5.0
    workspaces: dict[str, float] = Field(default_factory=dict)  # slug -> USD/day


class ServerCfg(BaseModel):
    base_url: str = "http://localhost:8000"
    service_token_env: str = "TT_LLM_SERVICE_TOKEN"  # noqa: S105
    ca_bundle: str | None = None


class AuthCfg(BaseModel):
    tokens_env: str = "TT_LLM_GATEWAY_TOKENS"  # noqa: S105 - comma separated shared secrets
    admin_env: str = "TT_LLM_ADMIN_TOKEN"  # noqa: S105 - needed for GET /v1/runs/{id}
    allow_anonymous: bool = False  # laptop sidecar bound to loopback only


class StorageCfg(BaseModel):
    url: str | None = None  # SQLAlchemy async URL; None = in-memory (sidecar / tests)
    schema_name: str = "llm"


class GatewayConfig(BaseModel):
    privacy_mode: PrivacyMode = "hybrid"
    cloud_aliases: list[str] = Field(
        default_factory=lambda: ["summariser"]
    )  # hybrid: which may leave the host
    ollama_api_base: str = "http://localhost:11434"
    aliases: dict[str, AliasCfg] = Field(default_factory=dict)
    budgets: BudgetCfg = Field(default_factory=BudgetCfg)
    limits: Limits = Field(default_factory=Limits)
    server: ServerCfg = Field(default_factory=ServerCfg)
    auth: AuthCfg = Field(default_factory=AuthCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    prompt_versions: dict[str, str] = Field(default_factory=dict)  # task -> version, default "v1"
    log_raw_input: bool = False

    @model_validator(mode="after")
    def _local_only_is_loopback(self) -> GatewayConfig:
        if self.privacy_mode == "local_only" and urlparse(self.ollama_api_base).hostname not in LOOPBACK:
            raise ValueError("privacy_mode=local_only requires ollama_api_base on localhost")
        return self


DEFAULT_ALIASES: dict[str, dict[str, Any]] = {
    "translator": {"primary": "ollama/qwen2.5:7b-instruct", "fallbacks": ["ollama/llama3.1:8b"]},
    "agent": {"primary": "ollama/qwen2.5:14b-instruct", "fallbacks": ["anthropic/claude-sonnet-5-5"]},
    "summariser": {"primary": "anthropic/claude-sonnet-5-5", "fallbacks": ["ollama/qwen2.5:14b-instruct"]},
    "reasoner": {"primary": "anthropic/claude-opus-5-5"},
    "embedder": {"primary": "ollama/nomic-embed-text"},
}


def default_config(**over: Any) -> GatewayConfig:
    return GatewayConfig.model_validate({"aliases": DEFAULT_ALIASES, **over})


def load_config(path: str | Path | None = None) -> GatewayConfig:
    path = path or os.environ.get("TT_LLM_CONFIG")
    data: dict[str, Any] = {}
    if path and Path(path).is_file():
        data = yaml.safe_load(Path(path).read_text()) or {}
    data.setdefault("aliases", DEFAULT_ALIASES)
    if mode := os.environ.get("TT_LLM_PRIVACY_MODE"):
        data["privacy_mode"] = mode
    if url := os.environ.get("TT_LLM_STORAGE_URL"):
        data.setdefault("storage", {})["url"] = url
    if base := os.environ.get("TT_LLM_SERVER_URL"):
        data.setdefault("server", {})["base_url"] = base
    return GatewayConfig.model_validate(data)
