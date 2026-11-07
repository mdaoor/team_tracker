"""LLM translation through the gateway (Phase 2b), with deterministic post-processing and fallback.

No provider SDK or Ollama call lives here: the gateway owns models, prompts and privacy modes. This module
only (1) calls it, (2) repairs/validates what comes back, (3) falls back to the structured parser.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import ValidationError
from tt_schema.base import new_id
from tt_schema.client import ClientBatch, ClientMessage

from tt_client.pack import ContextPack
from tt_client.parser.structured import ParseContext, StructuredParser

CONFIDENCE_FLOOR = 0.7
OVERLAY_FIELDS = (
    "parent",
    "item_type",
    "priority",
    "milestone",
    "iteration",
    "stage",
    "product",
    "risks",
    "assignees",
    "custom_fields",
)


class LLMTranslator(Protocol):
    def translate(self, line: str, pack: ContextPack | None, ctx: ParseContext) -> ClientBatch | None: ...


class NullTranslator:
    """Structured parse only (no model available or configured)."""

    def translate(self, line: str, pack: ContextPack | None, ctx: ParseContext) -> ClientBatch | None:
        return None


class GatewayTranslator:
    def __init__(
        self,
        gateway_url: str,
        *,
        mode: str = "translate",
        timeout: float = 1.5,
        token: str | None = None,
        client: httpx.Client | None = None,
        verify: ssl.SSLContext | str | bool = True,
        workspace: str = "",
        core_token: str | None = None,
    ) -> None:
        self.url = gateway_url.rstrip("/")
        self.mode = mode
        self.workspace = workspace
        self.core_token = (
            core_token  # only ever sent in agent mode, where the gateway calls tools as the caller
        )
        self.http = client or httpx.Client(
            timeout=timeout, verify=verify, headers={"Authorization": f"Bearer {token}"} if token else {}
        )

    def translate(self, line: str, pack: ContextPack | None, ctx: ParseContext) -> ClientBatch | None:
        endpoint = "/v1/agent/capture" if self.mode == "agent" else "/v1/translate"
        body = {
            "line": line,
            "actor": ctx.actor,
            "now": ctx.now.isoformat(),
            "time_zone": ctx.tz,
            "pack": pack.compact() if pack else {},
            "workspace": self.workspace,
        }
        headers = {"X-TT-Core-Token": self.core_token} if self.mode == "agent" and self.core_token else {}
        try:
            r = self.http.post(self.url + endpoint, json=body, headers=headers)
            r.raise_for_status()
            payload = r.json()
            return ClientBatch.model_validate(payload.get("batch", payload))
        except (httpx.HTTPError, ValueError, ValidationError):
            return None


def batch_review(gateway: GatewayTranslator, batches: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """`tt sync --assist`: ask the gateway to merge fragments / fill gaps in pending batches (Phase 2b)."""
    try:
        r = gateway.http.post(
            gateway.url + "/v1/agent/batch-review", json={"batches": batches, "workspace": gateway.workspace}
        )
        r.raise_for_status()
        cleaned = r.json()["batches"]
        for b in cleaned:
            ClientBatch.model_validate(b)
        return list(cleaned)
    except (httpx.HTTPError, ValueError, KeyError, ValidationError):
        return None


@dataclass
class Translation:
    batch: ClientBatch
    source: str  # "llm" | "structured"
    notes: list[str] = field(default_factory=list)


def postprocess(
    batch: ClientBatch,
    structured: list[ClientMessage],
    pack: ContextPack | None,
    ctx: ParseContext,
    line: str,
) -> tuple[ClientBatch, list[str]]:
    """Repair: parser numbers/dates/tags win; unknown entities are dropped; weak hints are nulled."""
    notes: list[str] = []
    people = set(pack.people_handles) if pack else None
    milestones = set(pack.milestone_names) if pack else None
    iterations = set(pack.iteration_names) if pack else None
    risks = set(pack.risk_keys) if pack else None
    open_keys = {i["key"] for i in pack.open_items} if pack else None
    out: list[ClientMessage] = []
    for i, msg in enumerate(batch.messages):
        m = msg.model_copy(deep=True)
        m.actor, m.at, m.client_id, m.raw_input = ctx.actor, ctx.now, new_id(), line
        det = structured[i] if i < len(structured) else (structured[0] if structured else None)
        if det is not None:
            for f in ("minutes", "planned_end", "estimate"):
                dv = getattr(det, f)
                if dv is not None and dv != getattr(m, f):
                    if getattr(m, f) is not None:
                        notes.append(f"{f}: parser value used over model value")
                    setattr(m, f, dv)
            if det.tags:
                m.tags = det.tags
            if det.item and det.item.key:
                m.item = det.item
            if det.on_behalf_of:
                m.on_behalf_of = det.on_behalf_of
        if (
            m.item
            and m.item.key
            and open_keys is not None
            and m.item.key not in open_keys
            and not (det and det.item and det.item.key)
        ):
            m.item.key = None
            notes.append("dropped item key absent from the pack")
        if people is not None:
            if m.on_behalf_of and m.on_behalf_of not in people:
                m.on_behalf_of = None
                notes.append("dropped unknown person")
            kept = [a for a in m.assignees if a.get("person") in people]
            if len(kept) != len(m.assignees):
                notes.append("dropped unknown assignee")
            m.assignees = kept
        if milestones is not None and m.milestone and m.milestone not in milestones:
            m.milestone = None
            notes.append("dropped unknown milestone")
        if iterations is not None and m.iteration and m.iteration not in iterations:
            m.iteration = None
            notes.append("dropped unknown iteration")
        if risks is not None and m.risks:
            kept_r = [r for r in m.risks if r in risks]
            if len(kept_r) != len(m.risks):
                notes.append("dropped unknown risk")
            m.risks = kept_r
        for fld in OVERLAY_FIELDS:
            conf = (m.llm_confidence or {}).get(fld)
            if conf is not None and conf < CONFIDENCE_FLOOR and getattr(m, fld):
                setattr(
                    m,
                    fld,
                    []
                    if isinstance(getattr(m, fld), list)
                    else {}
                    if isinstance(getattr(m, fld), dict)
                    else None,
                )
                notes.append(f"nulled low-confidence {fld}")
        out.append(m)
    return batch.model_copy(update={"messages": out}), notes


def translate_line(
    line: str,
    ctx: ParseContext,
    *,
    pack: ContextPack | None,
    translator: LLMTranslator | None,
    mode: str = "assisted",
) -> Translation:
    parsed = StructuredParser(ctx).parse(line)
    notes = list(parsed.notes)
    batch: ClientBatch | None = None
    if translator is not None and parsed.messages:
        batch = translator.translate(line, pack, ctx)
    if batch is not None and batch.messages:
        batch, extra = postprocess(batch, parsed.messages, pack, ctx, line)
        return Translation(batch.model_copy(update={"mode": mode}), "llm", notes + extra)
    if translator is not None and not isinstance(translator, NullTranslator):
        notes.append("model unavailable: used the structured parser")
    return Translation(
        ClientBatch(workspace="", messages=parsed.messages, mode=mode),  # workspace filled by the caller
        "structured",
        notes,
    )
