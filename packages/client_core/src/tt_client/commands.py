"""Management commands on the client (Phase 14): building `ClientCommand`s, mixed batches, confirmation and effect rows.

Capture never blocks; commands MAY be rejected (ADR-0052). Everything here is plain data so the CLI, the desktop sidecar and
the Slack bot share it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tt_schema.base import new_id
from tt_schema.client import CLIENT_SCHEMA_VERSION, ClientBatch, ClientCommand, ClientMessage

from tt_client.catalogue import OpMeta

CLIENT_VERSION = CLIENT_SCHEMA_VERSION
META = "_meta"
BULK_THRESHOLD = 5  # stage.move / item.tag / ... over more keys than this count as destructive (wide)
BULK_OPS = frozenset({"stage.move", "item.tag", "item.move_to_iteration", "item.reparent", "review.bulk"})
PREVIEW_SUFFIX = ":preview"


def meta_block(*, mode: str, role_mode: str | None, forced: bool = False) -> dict[str, Any]:
    """`args["_meta"]`: recorded by the server in the event payload (audit)."""
    return {"client_version": CLIENT_VERSION, "mode": mode, "role_mode": role_mode, "forced": forced}


def strip_meta(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k != META}


def new_command(
    op: str,
    args: dict[str, Any],
    *,
    actor: str,
    now: datetime,
    mode: str = "manual",
    role_mode: str | None = None,
    on_behalf_of: str | None = None,
    forced: bool = False,
    client_id: str | None = None,
) -> ClientCommand:
    return ClientCommand(
        client_id=client_id or new_id(),
        actor=actor,
        at=now,
        op=op,
        args={**strip_meta(args), META: meta_block(mode=mode, role_mode=role_mode, forced=forced)},
        on_behalf_of=on_behalf_of or None,
    )


def mark_forced(cmd: ClientCommand) -> ClientCommand:
    """`--force`: the person overrode the destructive-op guard; the server logs `_meta.forced`."""
    meta = {**cmd.args.get(META, {}), "forced": True}
    return cmd.model_copy(update={"args": {**cmd.args, META: meta}})


def is_forced(cmd: ClientCommand) -> bool:
    return bool((cmd.args.get(META) or {}).get("forced"))


def preview_copy(cmd: ClientCommand) -> ClientCommand:
    """A `preview_only` twin with its own idempotency key, so a preview can never shadow the real command."""
    return cmd.model_copy(update={"client_id": cmd.client_id + PREVIEW_SUFFIX, "preview_only": True})


def make_batch(
    workspace: str,
    *,
    messages: list[ClientMessage] | None = None,
    commands: list[ClientCommand] | None = None,
    mode: str | None = None,
) -> ClientBatch:
    """One batch for capture lines and commands alike (server order: messages first, then commands)."""
    return ClientBatch(
        workspace=workspace,
        messages=messages or [],
        commands=commands or [],
        client_version=CLIENT_VERSION,
        mode=mode if mode in ("assisted", "manual") else None,
    )


def batch_id(batch: dict[str, Any]) -> str:
    """The outbox key: first message, else first command."""
    if batch.get("messages"):
        return str(batch["messages"][0]["client_id"])
    if batch.get("commands"):
        return str(batch["commands"][0]["client_id"])
    raise ValueError("empty batch: no messages and no commands")


# ------------------------------------------------------------------ safety
def is_destructive(meta: OpMeta, args: dict[str, Any] | None = None) -> bool:
    """The catalogue flag, plus wide bulk actions (more than BULK_THRESHOLD targets)."""
    if meta.destructive:
        return True
    if args and meta.op in BULK_OPS:
        for k in ("keys", "rows"):
            v = args.get(k)
            if isinstance(v, list) and len(v) > BULK_THRESHOLD:
                return True
    return False


def needs_confirmation(
    meta: OpMeta, yes: bool = False, force: bool = False, args: dict[str, Any] | None = None
) -> bool:
    """Must a person still say "yes" interactively?

    * ordinary command: `--yes` is enough;
    * destructive or wide command: `--yes` alone does NOT bypass; only `--force` does (and the send is logged as forced).
    """
    if is_destructive(meta, args):
        return not force
    return not (yes or force)


# ------------------------------------------------------------------ results
@dataclass
class EffectRow:
    label: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def render_effect(preview: dict[str, Any] | None) -> list[EffectRow]:
    """Rows for the "Effect preview" section of a command card."""
    if not preview:
        return []
    rows: list[EffectRow] = []
    if preview.get("will_create"):
        rows.append(EffectRow("Creates", ", ".join(str(x) for x in preview["will_create"])))
    if preview.get("will_delete"):
        rows.append(EffectRow("Removes", ", ".join(str(x) for x in preview["will_delete"])))
    if preview.get("affected"):
        rows.append(EffectRow("Affects", _plural(int(preview["affected"]), "item")))
    if preview.get("children"):
        rows.append(EffectRow("Children", _plural(int(preview["children"]), "child item")))
    if preview.get("overrides_kept"):
        rows.append(EffectRow("Overrides kept", str(int(preview["overrides_kept"]))))
    sample = preview.get("sample") or []
    if sample:
        rows.append(EffectRow("Sample", "; ".join(_sample_text(x) for x in sample[:3])))
    return rows


def _sample_text(x: Any) -> str:
    if isinstance(x, dict):
        return " ".join(str(v) for v in list(x.values())[:2])
    return str(x)


def effect_summary(preview: dict[str, Any] | None) -> str:
    """One line: "creates project PAY · adds 3 members"."""
    if not preview:
        return ""
    parts = [f"creates {x}" for x in preview.get("will_create") or []]
    parts += [f"removes {x}" for x in preview.get("will_delete") or []]
    if preview.get("affected"):
        parts.append(f"affects {_plural(int(preview['affected']), 'item')}")
    if preview.get("children"):
        parts.append(_plural(int(preview["children"]), "child item"))
    if preview.get("overrides_kept"):
        parts.append(f"{int(preview['overrides_kept'])} override(s) kept")
    return " · ".join(parts)


@dataclass
class CommandResult:
    client_id: str
    status: str  # applied | rejected | previewed | deduplicated
    result: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_doc(cls, d: dict[str, Any]) -> CommandResult:
        return cls(
            client_id=str(d.get("client_id", "")),
            status=str(d.get("status", "")),
            result=d.get("result"),
            preview=d.get("preview"),
            errors=list(d.get("errors") or []),
        )

    @property
    def ok(self) -> bool:
        return self.status in ("applied", "deduplicated", "previewed")

    def reason(self) -> str:
        return "; ".join(
            f"{e.get('message', e.get('code', ''))}" + (f" ({e['pointer']})" if e.get("pointer") else "")
            for e in self.errors
        )
