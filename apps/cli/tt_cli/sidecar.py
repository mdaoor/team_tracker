"""`tt-sidecar` - the one executable the desktop and mobile shells call (ADR-0038: no business logic in Rust or TypeScript).

    tt-sidecar <verb> '<json args>'     one request, one JSON document on stdout (what the Tauri shell does)
    tt-sidecar <verb>                   the JSON args are read from stdin
    tt-sidecar --serve                  long-lived: one JSON request per stdin line, one JSON answer per stdout line

A request line for `--serve` is `{"verb": "parse", "args": {...}}`; the answer is the verb's document. stdout carries nothing but JSON
(logs and tracebacks go to stderr) so the shell never parses prose. Verbs:

* capture (palette): `parse pack send build-manual outbox resend discard open-editor active`
* management commands (phase 14): `cmd-*` (see `tt_client.sidecar_commands.VERBS`)
* personal tracker (phase 15): `personal_*` (see `tt_client.personal.bridge.COMMANDS`)

Handled failures are `{"error": ...}` documents with exit code 0 (the shell shows them); a request that cannot even be read exits 2.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from tt_client import form as tform
from tt_client import roles
from tt_client.api import TTApi
from tt_client.catalogue import CatalogueCache
from tt_client.config import ClientConfig
from tt_client.outbox import Entry, Outbox, sync
from tt_client.parser.llm import NullTranslator, translate_line
from tt_client.personal.bridge import COMMANDS as PERSONAL_COMMANDS
from tt_client.personal.bridge import handle as personal_handle
from tt_client.preview import preview_dict
from tt_client.sidecar_commands import VERBS as COMMAND_VERBS
from tt_client.sidecar_commands import CommandContext
from tt_client.sidecar_commands import dispatch as command_dispatch
from tt_schema.client import ClientBatch

from tt_cli import commands_cli
from tt_cli.personal import tracker
from tt_cli.runtime import Runtime, load_runtime, now

CAPTURE_VERBS = (
    "parse", "pack", "send", "build-manual", "outbox", "resend", "discard", "open-editor", "active",
)  # fmt: skip


def verbs() -> list[str]:
    return [*CAPTURE_VERBS, *COMMAND_VERBS, *PERSONAL_COMMANDS]


# ------------------------------------------------------------------ capture verbs


def _batch_doc(batch: ClientBatch) -> dict[str, Any]:
    return batch.model_dump(mode="json", exclude_none=True)


def _summary(doc: dict[str, Any]) -> str:
    parts: list[str] = []
    for m in doc.get("messages", []):
        item = m.get("item") or {}
        parts.append(f"{m.get('action', '')} {item.get('key') or item.get('title') or ''}".strip())
    for c in doc.get("commands", []):
        parts.append(str(c.get("op", "")))
    return "; ".join(parts) or "(empty)"


def _entry_row(rt: Runtime, e: Entry) -> dict[str, Any]:
    try:
        summary = _summary(rt.outbox.load(e))
    except (OSError, ValueError):
        summary = "(unreadable file)"
    row: dict[str, Any] = {
        "id": e.batch_id,
        "status": "failed" if e.last_error and not e.last_error.startswith("offline") else "pending",
        "summary": summary,
        "path": str(e.path),
    }
    if e.last_error:
        row["error"] = e.last_error
    return row


def _send_result(rt: Runtime, entries: list[Entry]) -> dict[str, Any]:
    report = sync(rt.api, rt.outbox, only=entries)
    if report.offline:
        return {"status": "queued", "results": []}
    if report.quarantined:
        return {"status": "failed", "results": [], "error": "the server rejected the file (schema violation)"}
    failed = report.failed_messages > 0
    return {"status": "failed" if failed else "sent", "results": report.results or []}


def _parse(rt: Runtime, a: dict[str, Any]) -> dict[str, Any]:
    from tt_cli.main import _set_jira

    pack = rt.pack()
    translator = rt.translator() if a.get("llm", True) else NullTranslator()
    t = translate_line(
        str(a.get("text", "")),
        rt.parse_ctx(),
        pack=pack,
        translator=translator,
        mode=str(a.get("mode", "assisted")),
    )
    batch = t.batch.model_copy(update={"workspace": rt.cfg.workspace})
    _set_jira(batch, pack.data if pack else None, bool(a.get("jira")))
    return {
        "cards": preview_dict(batch.messages, pack)["cards"],
        "source": t.source,
        "batch": _batch_doc(batch),
        "notes": t.notes,
    }


def _send(rt: Runtime, a: dict[str, Any]) -> dict[str, Any]:
    from tt_cli.main import _set_jira

    try:
        batch = Outbox.validate(dict(a.get("batch") or {}))
    except ValueError as exc:
        return {"status": "failed", "results": [], "error": f"invalid ClientBatch: {exc}"}
    pack = rt.pack(refresh=False)
    _set_jira(batch, pack.data if pack else None, bool(a.get("jira")))
    entry = rt.outbox.write(
        _batch_doc(batch), preview_dict(batch.messages, pack)
    )  # on disk first: nothing is lost offline
    return _send_result(rt, [entry])


def _build_manual(rt: Runtime, a: dict[str, Any]) -> dict[str, Any]:
    pack = rt.pack(refresh=False)
    if pack is None:
        return {"error": "no ContextPack yet: connect once so the choices can be fetched"}
    answers = dict(a.get("answers") or {})
    missing = tform.missing_required(answers, pack)
    if missing:
        return {"error": f"missing mandatory field(s): {', '.join(missing)}", "missing": missing}
    try:
        msg = tform.build_message(
            answers, actor=rt.cfg.handle, now=now(), tz=rt.cfg.time_zone, hours_per_day=rt.cfg.hours_per_day
        )
    except (KeyError, ValueError) as exc:
        return {"error": f"cannot build the message: {exc}"}
    return _batch_doc(tform.to_batch([msg], rt.cfg.workspace))


def _find(rt: Runtime, a: dict[str, Any]) -> Entry | dict[str, Any]:
    try:
        return rt.outbox.find(str(a.get("id", "")))
    except KeyError as exc:
        return {"error": str(exc.args[0])}


def _open_editor(rt: Runtime, a: dict[str, Any]) -> dict[str, Any]:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.TemporaryDirectory(dir=rt.data) as tmp:
        path = Path(tmp) / "batch.json"
        path.write_text(json.dumps(a.get("batch") or {}, indent=2))
        subprocess.run([*shlex.split(editor), str(path)], check=True)  # noqa: S603
        try:
            doc = Outbox.load_file(path)
            Outbox.validate(doc)
        except ValueError as exc:
            return {"error": f"invalid ClientBatch after editing: {exc}"}
        return doc


def capture(rt: Runtime, verb: str, a: dict[str, Any]) -> Any:
    if verb == "parse":
        return _parse(rt, a)
    if verb == "pack":
        pack = rt.pack()
        return pack.data if pack else None
    if verb == "send":
        return _send(rt, a)
    if verb == "build-manual":
        return _build_manual(rt, a)
    if verb == "outbox":
        return [_entry_row(rt, e) for e in rt.outbox.pending()]
    if verb == "resend":
        found = _find(rt, a)
        return found if isinstance(found, dict) else _send_result(rt, [found])
    if verb == "discard":
        found = _find(rt, a)
        if isinstance(found, dict):
            return found
        rt.outbox.discard(found)
        return {"discarded": found.batch_id}
    if verb == "open-editor":
        return _open_editor(rt, a)
    if verb == "active":
        pack = rt.pack(refresh=False)
        return pack.current_activity if pack else None
    return {"error": f"unknown verb {verb!r}"}


# ------------------------------------------------------------------ dispatch


def _command_context(rt: Runtime) -> CommandContext:
    state = {"role": commands_cli.current_mode(rt)}

    def set_role(mode: str) -> None:
        state["role"] = mode
        commands_cli.set_config_role(mode)

    return CommandContext(
        api=rt.api,
        outbox=rt.outbox,
        catalogue_cache=CatalogueCache(rt.data / "catalogue.json"),
        role_cache=roles.RoleCache(rt.data / "me.json"),
        pack=lambda: rt.pack(refresh=False),
        actor=rt.cfg.handle,
        workspace=rt.cfg.workspace,
        now=now,
        tz=rt.cfg.time_zone,
        translator=commands_cli._translator(rt),
        get_role=lambda: state["role"],
        set_role=set_role,
    )


def handle(verb: str, args: dict[str, Any]) -> Any:
    """Run one verb and return its JSON-able answer. Never prints."""
    if verb in PERSONAL_COMMANDS:
        cfg = ClientConfig.load()
        api = TTApi(cfg) if (cfg.workspace and cfg.handle and cfg.token) else None
        return personal_handle(tracker(), verb, args, api)
    rt = load_runtime(require_login=False)
    if verb in CAPTURE_VERBS:
        return capture(rt, verb, args)
    if verb in COMMAND_VERBS:
        return command_dispatch(verb, args, _command_context(rt))
    return {"error": f"unknown verb {verb!r}"}


def _answer(verb: str, args: dict[str, Any], handler: Callable[[str, dict[str, Any]], Any]) -> Any:
    try:
        with contextlib.redirect_stdout(
            sys.stderr
        ):  # stdout is the JSON channel: stray prints must not corrupt it
            return handler(verb, args)
    except Exception as exc:  # noqa: BLE001 - the shell must always get a JSON document
        print(f"tt-sidecar: {verb}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {"error": f"{type(exc).__name__}: {exc}"}


def _args(text: str) -> dict[str, Any]:
    doc = json.loads(text) if text.strip() else {}
    if not isinstance(doc, dict):
        raise ValueError("the arguments must be a JSON object")
    return doc


def serve(stdin: TextIO, stdout: TextIO, handler: Callable[[str, dict[str, Any]], Any] = handle) -> int:
    for line in stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            verb, args = str(req["verb"]), dict(req.get("args") or {})
        except (ValueError, KeyError, TypeError) as exc:
            out: Any = {"error": f"bad request line: {exc}"}
        else:
            out = _answer(verb, args, handler)
        stdout.write(json.dumps(out, default=str) + "\n")
        stdout.flush()
    return 0


def main(argv: list[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    if argv[:1] == ["--serve"]:
        return serve(stdin, stdout)
    if argv[:1] in ([], ["-h"], ["--help"]):
        print(__doc__, file=sys.stderr)
        stdout.write(
            json.dumps({"error": "usage: tt-sidecar <verb> [json] | --serve", "verbs": verbs()}) + "\n"
        )
        return 2
    if argv[:1] == ["--verbs"]:
        stdout.write(json.dumps(verbs()) + "\n")
        return 0
    verb = argv[0]
    try:
        args = _args(argv[1] if len(argv) > 1 else stdin.read())
    except ValueError as exc:
        stdout.write(json.dumps({"error": f"bad arguments: {exc}"}) + "\n")
        return 2
    stdout.write(json.dumps(_answer(verb, args, handle), default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
