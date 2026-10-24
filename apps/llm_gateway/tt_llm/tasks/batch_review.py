"""`POST /v1/agent/batch-review`: tidy the reporter's pending outbox files (Phase 2b §2b.4).

Merging, de-duplication, project filling and normalisation are deterministic code; the model is only asked
the one thing code cannot know -- which project an unplaced line belongs to -- and its answer is validated
against the pack. The reporter owns the files: this returns a proposal, the client shows the diff and
rewrites nothing without a yes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from tt_client.pack import ContextPack
from tt_schema.client import ClientAction, ClientBatch, ClientMessage

from tt_llm.guards import wrap_untrusted
from tt_llm.routing import GatewayError
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt

MERGE_WINDOW = timedelta(hours=2)
DUP_WINDOW = timedelta(hours=24)
ANCHORS = {ClientAction.START, ClientAction.RESUME, ClientAction.CREATE}
FRAGMENTS = {ClientAction.NOTE, ClientAction.LOG_TIME}


@dataclass
class _Slot:
    file: int
    msg: ClientMessage
    changes: list[str] = field(default_factory=list)
    dropped: bool = False
    touched: bool = False


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    d = {k: v for k, v in d.items() if not k.startswith("_")}
    d["messages"] = [{k: v for k, v in m.items() if not k.startswith("_")} for m in d.get("messages", [])]
    return d


def _identity(m: ClientMessage) -> bool:
    return bool(m.item and (m.item.key or m.item.id or m.item.title))


def _norm(t: str | None) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _merge_fragments(slots: list[_Slot]) -> list[str]:
    warnings: list[str] = []
    ordered = sorted(slots, key=lambda s: s.msg.at)
    for x in ordered:
        m = x.msg
        if x.dropped or m.action not in FRAGMENTS or _identity(m) or m.project:
            continue
        anchor = next(
            (
                p
                for p in reversed(ordered)
                if p is not x
                and not p.dropped
                and p.msg.actor == m.actor
                and p.msg.action in ANCHORS
                and _identity(p.msg)
                and timedelta(0) <= m.at - p.msg.at <= MERGE_WINDOW
            ),
            None,
        )
        if anchor is None:
            continue
        a = anchor.msg
        if m.minutes:
            a.minutes = (a.minutes or 0) + m.minutes
        if m.note:
            a.note = f"{a.note}; {m.note}" if a.note else m.note
        a.tags = sorted({*a.tags, *m.tags})
        if m.evidence:
            a.evidence = [*(a.evidence or []), *m.evidence]
        a.planned_end = a.planned_end or m.planned_end
        a.estimate = a.estimate or m.estimate
        what = f"{m.minutes}m" if m.minutes else "note"
        anchor.changes.append(f"merged {what} from {m.client_id} into this {a.action.value}")
        anchor.touched = True
        x.dropped, x.touched = True, True
        x.changes.append(f"merged into {a.client_id}")
    return warnings


def _drop_duplicates(slots: list[_Slot]) -> list[str]:
    warnings: list[str] = []
    seen: list[_Slot] = []
    for x in sorted((s for s in slots if not s.dropped), key=lambda s: s.msg.at):
        m = x.msg
        key = _norm(m.item.title if m.item and m.item.title else None)
        if m.action != ClientAction.CREATE or not key:
            continue
        twin = next(
            (
                s
                for s in seen
                if s.msg.actor == m.actor
                and _norm(s.msg.item.title if s.msg.item else None) == key
                and timedelta(0) <= m.at - s.msg.at <= DUP_WINDOW
            ),
            None,
        )
        if twin is None:
            seen.append(x)
            continue
        x.dropped, x.touched = True, True
        x.changes.append(f"duplicate of {twin.msg.client_id}")
        twin.changes.append(f"absorbed duplicate {m.client_id}")
        twin.touched = True
        warnings.append(f"dropped duplicate {m.client_id}")
    return warnings


def _fill_projects(slots: list[_Slot], pack: ContextPack | None) -> list[_Slot]:
    """Deterministic: glossary / alias phrases in the text. Returns the slots still without a project."""
    open_slots: list[_Slot] = []
    for x in slots:
        m = x.msg
        if x.dropped or m.project or (m.item and m.item.key):
            continue
        text = " ".join(filter(None, [m.item.title if m.item else None, m.note, m.raw_input])).lower()
        found = None
        if pack is not None:
            for p in pack.data.get("projects", []):
                for alias in {
                    p["key"].lower(),
                    p["name"].lower(),
                    *(a.lower() for a in p.get("aliases", [])),
                }:
                    if alias and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text):
                        found = p["key"]
                        break
                if found:
                    break
            if not found:
                for phrase, target in (pack.data.get("glossary") or {}).items():
                    if phrase.lower() in text and str(target).startswith("PROJECT:"):
                        found = str(target).split(":", 1)[1]
                        break
        if found:
            m.project = found
            x.changes.append(f"project set to {found} from the pack")
            x.touched = True
        else:
            open_slots.append(x)
    return open_slots


async def _model_fill(
    deps: Deps, req: TaskRequest, run: Any, slots: list[_Slot], pack: ContextPack | None
) -> list[str]:
    if pack is None or not slots:
        return []
    keys = {p["key"] for p in pack.data.get("projects", [])}
    candidates = [
        {"key": p["key"], "name": p["name"], "aliases": p.get("aliases", [])}
        for p in pack.data.get("projects", [])
    ]
    warnings: list[str] = []
    for x in slots[:10]:
        text = " ".join(filter(None, [x.msg.item.title if x.msg.item else None, x.msg.note]))
        if not text.strip():
            continue
        messages = [
            {"role": "system", "content": system_prompt(deps, "gap_fill")},
            {
                "role": "user",
                "content": wrap_untrusted("reporter_text", text) + "\ncandidates: " + json.dumps(candidates),
            },
        ]
        schema = {
            "type": "object",
            "properties": {"project": {"type": ["string", "null"]}, "confidence": {"type": "number"}},
            "required": ["project"],
        }
        try:
            data, comp = await deps.router.structured(
                "translator",
                messages,
                schema,
                workspace=req.workspace or "-",
                privacy_mode=req.options.privacy_mode,
            )
        except GatewayError as exc:
            warnings.append(f"model gap-fill skipped: {exc.code}")
            break
        run.model, run.cost_usd = comp.model, run.cost_usd + comp.cost_usd
        proj = data.get("project")
        if proj in keys and float(data.get("confidence") or 0) >= 0.7:
            x.msg.project = proj
            x.changes.append(f"project set to {proj} (model, confidence {data['confidence']})")
            x.touched = True
    return warnings


async def batch_review(deps: Deps, req: TaskRequest) -> TaskResponse:
    run = new_run("batch_review", req, "translator")
    pack = ContextPack(req.pack) if req.pack else None
    slots: list[_Slot] = []
    files: list[dict[str, Any]] = [{} for _ in req.batches]
    invalid: set[int] = set()
    meta: list[ClientBatch | None] = []
    for i, raw in enumerate(req.batches):
        try:
            b = ClientBatch.model_validate(_clean(raw))
        except ValidationError:
            invalid.add(i)
            meta.append(None)
            continue
        meta.append(b)
        slots += [_Slot(i, m) for m in b.messages]
    warnings = _merge_fragments(slots)
    warnings += _drop_duplicates(slots)
    open_slots = _fill_projects(slots, pack)
    warnings += await _model_fill(deps, req, run, open_slots, pack)
    for i, raw in enumerate(req.batches):
        if i in invalid:
            files[i] = {
                "index": i,
                "action": "keep",
                "batch": raw,
                "changes": ["not a valid ClientBatch: left untouched"],
            }
            warnings.append(f"file {i} left untouched (invalid)")
            continue
        mine = [s for s in slots if s.file == i]
        kept = [s for s in mine if not s.dropped]
        changes = [c for s in mine for c in s.changes]
        base = meta[i]
        assert base is not None  # noqa: S101
        action = "drop" if not kept else ("merge" if any(s.touched for s in mine) else "keep")
        files[i] = {
            "index": i,
            "action": action,
            "batch": base.model_copy(update={"messages": [s.msg for s in kept]}).model_dump(
                mode="json", exclude_none=True
            ),
            "changes": changes,
        }
    await finish(deps, run)
    return respond(
        run,
        output={"files": files},
        batches=[f["batch"] for f in files if f["action"] != "drop"],
        warnings=warnings,
    )
