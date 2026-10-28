"""The command flow shared by every shell: propose -> preview -> confirm -> send (plan 16, principle 1).

The CLI, the desktop sidecar and the Slack bot all call these functions, so a command looks and behaves the same everywhere.
Nothing here prints or prompts: callers render `Proposal`, `Outcome` and `ConfirmPlan` however their surface needs.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import ValidationError
from tt_schema.base import new_id
from tt_schema.client import ClientCommand, ClientMessage

from tt_client.api import ApiError, Unreachable
from tt_client.catalogue import Catalogue
from tt_client.commands import (
    META,
    CommandResult,
    EffectRow,
    effect_summary,
    is_destructive,
    make_batch,
    mark_forced,
    meta_block,
    needs_confirmation,
    preview_copy,
    render_effect,
    strip_meta,
)
from tt_client.outbox import Entry, Outbox
from tt_client.pack import ContextPack
from tt_client.parser.commands import CommandParse, CommandParseContext, Incomplete, parse_commands


# ------------------------------------------------------------------ gateway
class GatewayCommandTranslator:
    """`POST /v1/translate/command` (or the agentic `/v1/agent/command`): free text -> ClientCommand[].

    The gateway needs the person's own token to read the catalogue and resolve names, so `X-TT-Core-Token` is always sent.
    It never applies anything; the client previews and confirms."""

    def __init__(
        self,
        gateway_url: str,
        *,
        mode: str = "translate",
        timeout: float = 8.0,
        token: str | None = None,
        client: httpx.Client | None = None,
        verify: ssl.SSLContext | str | bool = True,
        workspace: str = "",
        core_token: str | None = None,
    ) -> None:
        self.url = gateway_url.rstrip("/")
        self.mode, self.workspace, self.core_token = mode, workspace, core_token
        self.http = client or httpx.Client(
            timeout=timeout, verify=verify, headers={"Authorization": f"Bearer {token}"} if token else {}
        )

    def translate(
        self,
        line: str,
        pack: ContextPack | None,
        *,
        actor: str,
        now: datetime,
        tz: str,
        role_mode: str | None,
    ) -> tuple[list[dict[str, Any]], list[str]] | None:
        endpoint = "/v1/agent/command" if self.mode == "agent" else "/v1/translate/command"
        body = {
            "line": line,
            "actor": actor,
            "now": now.isoformat(),
            "time_zone": tz,
            "pack": pack.compact() if pack else {},
            "workspace": self.workspace,
            "role_mode": role_mode,
        }
        headers = {"X-TT-Core-Token": self.core_token} if self.core_token else {}
        try:
            r = self.http.post(self.url + endpoint, json=body, headers=headers)
            r.raise_for_status()
            payload = r.json()
            out = payload.get("output") or {}
            return list(out.get("commands", [])), list(payload.get("warnings", []))
        except (httpx.HTTPError, ValueError, AttributeError):
            return None


# ------------------------------------------------------------------ proposal
@dataclass
class Proposal:
    commands: list[ClientCommand] = field(default_factory=list)
    source: str = "none"  # parser | gateway | none
    notes: list[str] = field(default_factory=list)
    incomplete: list[Incomplete] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.commands)


def adopt_gateway_commands(
    raw: list[dict[str, Any]],
    cat: Catalogue,
    *,
    actor: str,
    now: datetime,
    role_mode: str | None,
    people: set[str] | None = None,
) -> tuple[list[ClientCommand], list[str]]:
    """Trust nothing the model said: keep only catalogue ops with schema-valid args; identity and `_meta` come from the client."""
    out: list[ClientCommand] = []
    notes: list[str] = []
    for item in raw:
        op, args = str(item.get("op", "")), strip_meta(dict(item.get("args") or {}))
        if op not in cat:
            notes.append(f"dropped {op!r}: not in the server's command catalogue")
            continue
        issues = cat.validate(op, args)
        if issues:
            notes.append(f"dropped {op}: " + "; ".join(str(i) for i in issues))
            continue
        behalf = item.get("on_behalf_of")
        if behalf and people is not None and behalf not in people:
            notes.append(f"dropped unknown person {behalf}")
            behalf = None
        try:
            out.append(
                ClientCommand(
                    client_id=new_id(),
                    actor=actor,
                    at=now,
                    op=op,
                    args={**args, META: meta_block(mode="assisted", role_mode=role_mode)},
                    on_behalf_of=behalf or None,
                )
            )
        except ValidationError as exc:  # pragma: no cover - defensive
            notes.append(f"dropped {op}: {exc.errors()[0]['msg']}")
    return out, notes


def propose(
    text: str,
    *,
    cat: Catalogue,
    pack: ContextPack | None,
    actor: str,
    now: datetime,
    tz: str = "UTC",
    role_mode: str | None = None,
    translator: GatewayCommandTranslator | None = None,
) -> Proposal:
    """Deterministic parser first (instant, offline); the gateway only for what the parser did not understand."""
    ctx = CommandParseContext(actor=actor, now=now, pack=pack, catalogue=cat, role_mode=role_mode)
    parsed: CommandParse = parse_commands(text, ctx)
    prop = Proposal(
        commands=list(parsed.commands),
        source="parser" if parsed.commands else "none",
        notes=list(parsed.notes),
        incomplete=list(parsed.incomplete),
        unmatched=list(parsed.unmatched),
    )
    if not parsed.unmatched or translator is None:
        if parsed.unmatched and translator is None:
            prop.notes.append("not understood locally and no gateway is configured: use `tt cmd <op> --form`")
        return prop
    rest = "; ".join(parsed.unmatched)
    got = translator.translate(rest, pack, actor=actor, now=now, tz=tz, role_mode=role_mode)
    if got is None:
        prop.notes.append("gateway unavailable: use `tt cmd <op> --form` for the rest")
        return prop
    raw, warnings = got
    people = set(pack.people_handles) if pack else None
    cmds, notes = adopt_gateway_commands(raw, cat, actor=actor, now=now, role_mode=role_mode, people=people)
    prop.commands += cmds
    prop.notes += warnings + notes
    prop.unmatched = [] if cmds else parsed.unmatched
    if cmds:
        prop.source = "gateway" if not parsed.commands else "parser+gateway"
    return prop


# ------------------------------------------------------------------ preview
@dataclass
class Outcome:
    """What the command card shows for one command."""

    command: ClientCommand
    status: str  # previewed | rejected | no_preview | offline
    preview: dict[str, Any] | None = None
    rows: list[EffectRow] = field(default_factory=list)
    summary: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    destructive: bool = False

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"

    def reason(self) -> str:
        return "; ".join(str(e.get("message", e.get("code", ""))) for e in self.errors)


def preview_commands(api: Any, cat: Catalogue, commands: list[ClientCommand]) -> list[Outcome]:
    """Ask the server what each command would do, without writing (`preview_only`). Offline degrades to a plain card."""
    outcomes: dict[str, Outcome] = {}
    to_ask: list[ClientCommand] = []
    for c in commands:
        meta = cat.get(c.op)
        destructive = bool(meta and is_destructive(meta, strip_meta(c.args)))
        if meta is not None and meta.preview:
            to_ask.append(c)
        outcomes[c.client_id] = Outcome(c, "no_preview", destructive=destructive)
    if not to_ask:
        return [outcomes[c.client_id] for c in commands]
    twins = {preview_copy(c).client_id: c for c in to_ask}
    try:
        status, body = api.post_commands([preview_copy(c).model_dump(mode="json") for c in to_ask])
    except (Unreachable, ApiError):
        for c in to_ask:
            outcomes[c.client_id].status = "offline"
        return [outcomes[c.client_id] for c in commands]
    if status not in (200, 207):
        for c in to_ask:
            o = outcomes[c.client_id]
            o.status, o.errors = "rejected", [{"code": f"http_{status}", "message": str(body)[:200]}]
        return [outcomes[c.client_id] for c in commands]
    for raw in body.get("results", []):
        res = CommandResult.from_doc(raw)
        src = twins.get(res.client_id)
        if src is None:
            continue
        o = outcomes[src.client_id]
        if res.status == "previewed":
            o.status, o.preview = "previewed", res.preview
            o.rows, o.summary = render_effect(res.preview), effect_summary(res.preview)
        elif res.status == "rejected":
            o.status, o.errors = "rejected", res.errors
    return [outcomes[c.client_id] for c in commands]


# ------------------------------------------------------------------ confirmation & staging
@dataclass
class ConfirmPlan:
    needed: bool  # ask a human (or refuse in a non-interactive run)
    destructive: list[str] = field(default_factory=list)  # ops that need the second, explicit confirmation
    forced: bool = False


def plan_confirmation(
    cat: Catalogue, commands: list[ClientCommand], *, yes: bool, force: bool
) -> ConfirmPlan:
    destructive: list[str] = []
    needed = False
    for c in commands:
        meta = cat.get(c.op)
        if meta is None:
            needed = True
            continue
        if is_destructive(meta, strip_meta(c.args)):
            destructive.append(c.op)
        if needs_confirmation(meta, yes=yes, force=force, args=strip_meta(c.args)):
            needed = True
    return ConfirmPlan(needed=needed, destructive=destructive, forced=force and bool(destructive))


def apply_force(cat: Catalogue, commands: list[ClientCommand], force: bool) -> list[ClientCommand]:
    """`--force` is recorded on exactly the commands it overrode (the destructive ones): `_meta.forced = true`."""
    if not force:
        return commands
    out = []
    for c in commands:
        meta = cat.get(c.op)
        out.append(mark_forced(c) if meta is not None and is_destructive(meta, strip_meta(c.args)) else c)
    return out


def stage(
    outbox: Outbox,
    workspace: str,
    commands: list[ClientCommand],
    *,
    messages: list[ClientMessage] | None = None,
    mode: str = "assisted",
    previews: list[Outcome] | None = None,
) -> Entry:
    """Write the (possibly mixed) batch to the outbox first, so nothing is lost offline."""
    batch = make_batch(workspace, messages=messages, commands=commands, mode=mode)
    doc = batch.model_dump(mode="json", exclude_none=True)
    preview = (
        {
            "commands": [
                {"client_id": o.command.client_id, "status": o.status, "preview": o.preview} for o in previews
            ]
        }
        if previews
        else None
    )
    return outbox.write(doc, preview)
