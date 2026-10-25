"""§12 client message contract (what the local LLM emits, what the server accepts)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import Field

from tt_schema.base import TTModel as _Client
from tt_schema.enums import ItemTypeKey, Priority, StrEnum

CLIENT_SCHEMA_VERSION = "0.2"  # ADR-0051: ClientBatch gains `commands[]`


class ClientAction(StrEnum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    DONE = "done"
    BLOCKED = "blocked"
    NOTE = "note"
    EVIDENCE = "evidence"
    ESTIMATE = "estimate"
    PLAN = "plan"
    CREATE = "create"
    UPDATE = "update"
    LOG_TIME = "log_time"


class ClientTaskRef(_Client):
    """How a client refers to an item: by key, by exact id, or by free-text title (fuzzy-matched)."""

    key: Optional[str] = None
    id: Optional[str] = None
    title: Optional[str] = None


class ClientMessage(_Client):
    """One line from the reporter, after the local LLM has structured it.
    Everything except actor/action/at/client_id is optional."""

    actor: str  # "@handle"
    action: ClientAction
    at: datetime
    client_id: str  # idempotency key
    item: Optional[ClientTaskRef] = None
    project: Optional[str] = None  # key / name / slug, resolved server-side
    tags: list[str] = Field(default_factory=list)
    note: Optional[str] = None
    minutes: Optional[int] = None  # "took 2h"
    planned_end: Optional[datetime] = None  # "plan to finish tomorrow 5pm"
    estimate: Optional[dict[str, Any]] = None  # {"value": 3, "unit": "hours"}
    evidence: Optional[list[dict[str, Any]]] = None
    on_behalf_of: Optional[str] = None  # "for: @sara"
    # overlay hints the LLM may fill from the manager-published context pack:
    parent: Optional[ClientTaskRef] = None
    item_type: Optional[ItemTypeKey] = None
    priority: Optional[Priority] = None
    milestone: Optional[str] = None
    iteration: Optional[str] = None
    stage: Optional[str] = None
    product: Optional[str] = None
    risks: list[str] = Field(default_factory=list)
    assignees: list[dict[str, str]] = Field(default_factory=list)  # [{"person":"@x","role":"owner"}]
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    raw_input: Optional[str] = None
    llm_confidence: Optional[dict[str, float]] = None  # per-field confidence
    jira: Optional[dict[str, Any]] = None  # {"project":"WEB","issuetype":"Task"} pass-through


class ClientCommand(_Client):
    """A management command (Phase 14, ADR-0051/0052): unlike a capture message it is authorised and validated
    server-side and MAY be rejected. `args` is validated against the op's `args_schema` from `GET /commands/catalogue`.

    `args["_meta"]` (optional) carries `{"client_version", "mode", "role_mode", "forced"}`; the server records it in the
    event payload (audit). `preview_only=True` asks for the effect without writing anything."""

    client_id: str  # idempotency key
    actor: str  # "@handle"
    at: datetime
    op: str  # e.g. "project.create"
    args: dict[str, Any] = Field(default_factory=dict)
    on_behalf_of: Optional[str] = None
    preview_only: bool = False


class ClientBatch(_Client):
    """A client can send several messages at once (offline sync, 'doing 1,2,3').

    Schema 0.2 (ADR-0051): `commands[]` sits next to `messages[]`; the server runs messages first, then commands, in order."""

    workspace: str
    messages: list[ClientMessage]
    client_version: str = (
        "0.1"  # spec default (parity); clients that know commands send CLIENT_SCHEMA_VERSION
    )
    commands: list[ClientCommand] = Field(
        default_factory=list
    )  # ADR-0051: schema addition, backwards compatible
    mode: Optional[Literal["assisted", "manual"]] = None  # ADR-0004: schema addition, backwards compatible


def _inline_refs(node: object, defs: dict[str, dict[str, object]]) -> object:
    """Resolve every `$ref` against `$defs` so the schema has no references (Ollama `format`)."""
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline_refs(merged, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _strip_titles(node: object) -> object:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title" or isinstance(v, dict)}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def client_message_json_schema(*, batch: bool = False) -> dict[str, object]:
    """Draft 2020-12 JSON Schema for `ClientMessage` (or `ClientBatch`), enums inline, no `$ref`.

    Suitable for Ollama's `format` parameter.
    """
    cls = ClientBatch if batch else ClientMessage
    raw = cls.model_json_schema(mode="validation")
    defs = raw.get("$defs", {})
    out = _inline_refs(raw, defs)
    assert isinstance(out, dict)
    out = _strip_titles(out)
    assert isinstance(out, dict)
    out["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return out
