"""Management commands from the same CLI (Phase 14): `tt role | do | cmd | preview | send | queue | team now | approve | waive | ops`.

Registered with one call from `main.py` (`register_commands(app)`), so the capture CLI stays untouched. Capture lines
(`tt "fixed login, took 2h"`) keep working in every role mode; commands are the only thing that may be rejected (ADR-0052)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.panel import Panel
from rich.table import Table
from tt_client import forms, roles
from tt_client.api import ApiError, Unreachable
from tt_client.catalogue import Catalogue, CatalogueCache, OpMeta
from tt_client.command_flow import (
    GatewayCommandTranslator,
    Outcome,
    Proposal,
    adopt_gateway_commands,
    apply_force,
    plan_confirmation,
    preview_commands,
    propose,
    stage,
)
from tt_client.commands import META, new_command, strip_meta
from tt_client.config import config_dir
from tt_client.outbox import Entry, SyncReport, sync
from tt_client.rows import ReviewRow, TeamNowRow
from tt_client.tls import verify_context
from tt_schema.client import ClientCommand, ClientMessage

from tt_cli import render
from tt_cli.runtime import Runtime, load_runtime, now

console = render.console

Yes = Annotated[
    bool, typer.Option("--yes", "-y", help="Send without asking (destructive ops still need --force)")
]
Force = Annotated[
    bool, typer.Option("--force", help="Also skip the destructive-op confirmation (logged as forced)")
]
NoSend = Annotated[bool, typer.Option("--no-send", help="Write the outbox file only")]
AsJson = Annotated[bool, typer.Option("--json", help="Print the batch JSON; send nothing")]
ForOpt = Annotated[str | None, typer.Option("--for", help="Act on behalf of a report, e.g. --for @sara")]


# ------------------------------------------------------------------ plumbing
def is_interactive() -> bool:
    """Is a person at the keyboard? (a seam so tests can drive the prompts)"""
    return sys.stdin.isatty()


def _cat_cache(rt: Runtime) -> CatalogueCache:
    return CatalogueCache(rt.data / "catalogue.json")


def _role_cache(rt: Runtime) -> roles.RoleCache:
    return roles.RoleCache(rt.data / "me.json")


def _catalogue(rt: Runtime) -> Catalogue:
    cat = _cat_cache(rt).refresh(rt.api)
    if cat is None:
        typer.echo(
            "No command catalogue yet. Connect once so `tt` can fetch it (or `tt ops --import catalogue.json`).",
            err=True,
        )
        raise typer.Exit(2)
    return cat


def current_mode(rt: Runtime) -> str:
    try:
        return roles.normalise(rt.cfg.role_mode)
    except ValueError:
        return roles.DEFAULT_MODE


def _translator(rt: Runtime) -> GatewayCommandTranslator | None:
    if not rt.cfg.gateway_url:
        return None
    return GatewayCommandTranslator(
        rt.cfg.gateway_url,
        mode=rt.cfg.llm_mode,
        timeout=max(rt.cfg.llm_agent_timeout if rt.cfg.llm_mode == "agent" else 8.0, 1.5),
        verify=verify_context(rt.cfg.ca_bundle),
        workspace=rt.cfg.workspace,
        core_token=rt.cfg.token or None,
    )


def set_config_role(mode: str) -> Path:
    """Persist `role_mode` in `[client]` without rewriting (or leaking env tokens into) the rest of the file."""
    path = config_dir() / "config.toml"
    text = path.read_text() if path.exists() else "[client]\n"
    line = f'role_mode = "{mode}"'
    if re.search(r"(?m)^role_mode\s*=", text):
        text = re.sub(r"(?m)^role_mode\s*=.*$", line, text)
    elif "[client]" in text:
        text = text.replace("[client]\n", f"[client]\n{line}\n", 1)
    else:
        text = text.rstrip("\n") + f"\n\n[client]\n{line}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


# ------------------------------------------------------------------ rendering
def _value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def command_card(o: Outcome, cat: Catalogue, role: str) -> Panel:
    c = o.command
    meta = cat.get(c.op)
    grid = Table.grid(padding=(0, 2))
    for k, v in strip_meta(c.args).items():
        grid.add_row(f"↳ {k.replace('_', ' ')}", _value(v))
    if c.on_behalf_of:
        grid.add_row("↳ on behalf of", c.on_behalf_of)
    grid.add_row("", "")
    grid.add_row("[bold]Effect preview[/bold]", "")
    if o.status == "previewed":
        for r in o.rows or []:
            grid.add_row(f"  {r.label}", r.value)
        if not o.rows:
            grid.add_row("  [dim]no visible change[/dim]", "")
    elif o.status == "rejected":
        grid.add_row("  [red]rejected[/red]", o.reason())
    elif o.status == "offline":
        grid.add_row("  [yellow]offline[/yellow]", "the server will validate this when it is sent")
    else:
        grid.add_row("  [dim]this op has no preview[/dim]", "")
    flags = " [red]destructive[/red]" if o.destructive else ""
    sub = f"role: {roles.label(role)}" + (f" · needs {meta.permission}" if meta and meta.permission else "")
    return Panel(grid, title=f"{c.op}{flags}", subtitle=sub, expand=False)


def show_outcomes(outcomes: list[Outcome], cat: Catalogue, role: str) -> None:
    for o in outcomes:
        console.print(command_card(o, cat, role))


def _confirm_interactive(plan_destructive: list[str]) -> bool:
    if not typer.confirm("Send?", default=False):
        return False
    if plan_destructive:
        typer.echo(
            f"This is destructive ({', '.join(sorted(set(plan_destructive)))}) and cannot be undone from the client."
        )
        return typer.confirm("Really send?", default=False)
    return True


def print_command_result(r: dict[str, Any], cmd: dict[str, Any]) -> None:
    """One line per command result: `✓ op`, or `✗ op rejected: <reason>` on stderr."""
    op = cmd["op"]
    if r["status"] == "rejected":
        why = "; ".join(
            e.get("message", e.get("code", "")) + (f" ({e['pointer']})" if e.get("pointer") else "")
            for e in r.get("errors", [])
        )
        typer.echo(f"✗ {op} rejected: {why}", err=True)
    else:
        flag = "✓ (already applied)" if r["status"] == "deduplicated" else "✓"
        typer.echo(f"{flag} {op}")


def _print_results(report: SyncReport, batch: dict[str, Any]) -> None:
    cmd_ids = {c["client_id"]: c for c in batch.get("commands", [])}
    for r in report.results or []:
        cid = r.get("client_id")
        if cid in cmd_ids:
            print_command_result(r, cmd_ids[cid])
        else:
            item = r.get("item") or {}
            flag = "✓" if r.get("status") in ("applied", "deduplicated", "applied_with_warnings") else "✗"
            typer.echo(f"{flag} {item.get('key', '')} {item.get('title', '')}".rstrip())


# ------------------------------------------------------------------ the shared run
def run_commands(
    rt: Runtime,
    cat: Catalogue,
    commands: list[ClientCommand],
    *,
    messages: list[ClientMessage] | None = None,
    mode: str = "manual",
    yes: bool = False,
    force: bool = False,
    no_send: bool = False,
    as_json: bool = False,
    source: str = "",
) -> None:
    """preview -> card -> stage in the outbox -> confirm -> send. Used by `do`, `cmd`, `approve`, `waive`, `queue`."""
    role = current_mode(rt)
    outcomes = preview_commands(rt.api, cat, commands)
    if as_json:
        batch = stage_doc(rt, commands, messages, mode)
        typer.echo(json.dumps(batch, indent=2, default=str))
        return
    if source:
        typer.echo(f"({source})")
    show_outcomes(outcomes, cat, role)
    rejected = [o for o in outcomes if o.rejected]
    if rejected:
        typer.echo(
            f"nothing was sent: the server would reject {len(rejected)} command(s) (see above).", err=True
        )
        raise typer.Exit(1)
    commands = apply_force(cat, commands, force)
    entry = stage(rt.outbox, rt.cfg.workspace, commands, messages=messages, mode=mode, previews=outcomes)
    if no_send:
        typer.echo(
            f"saved {entry.path}  (tt preview {entry.batch_id[:8]} · tt send {entry.batch_id[:8]} --yes)"
        )
        return
    deliver_entry(rt, cat, entry, yes=yes, force=force)


def stage_doc(
    rt: Runtime, commands: list[ClientCommand], messages: list[ClientMessage] | None, mode: str
) -> dict[str, Any]:
    from tt_client.commands import make_batch

    return make_batch(rt.cfg.workspace, messages=messages, commands=commands, mode=mode).model_dump(
        mode="json", exclude_none=True
    )


def deliver_entry(rt: Runtime, cat: Catalogue, entry: Entry, *, yes: bool, force: bool) -> None:
    doc = rt.outbox.load(entry)
    cmds = [ClientCommand.model_validate(c) for c in doc.get("commands", [])]
    plan = plan_confirmation(cat, cmds, yes=yes, force=force)
    if plan.needed:
        if not is_interactive():
            hint = "re-run with --yes" + (" --force (destructive)" if plan.destructive else "")
            typer.echo(
                f"needs confirmation and this is not a terminal: kept in the outbox; {hint}.", err=True
            )
            typer.echo(f"  {entry.path}", err=True)
            raise typer.Exit(3)
        if not _confirm_interactive(plan.destructive):
            typer.echo(f"kept in the outbox: {entry.path}")
            return
    elif plan.forced:
        typer.echo("forced: sending destructive command(s) without confirmation (logged).", err=True)
    rt.outbox.confirm(entry)  # the person said yes (or passed --yes/--force): `tt sync` may now deliver it
    report = sync(rt.api, rt.outbox, only=[entry])
    if report.offline:
        typer.echo(f"offline — queued {report.kept} batch(es); `tt sync` delivers them (exactly once).")
        return
    if report.quarantined:
        typer.echo(
            "the server rejected the file (schema violation) — it was quarantined; this is a client bug.",
            err=True,
        )
        raise typer.Exit(1)
    _print_results(report, doc)
    if report.rejected_commands or report.failed_messages:
        raise typer.Exit(1)


# ------------------------------------------------------------------ commands
def _incomplete_to_commands(rt: Runtime, cat: Catalogue, prop: Proposal, mode: str) -> list[ClientCommand]:
    """Recognised but incomplete phrases (e.g. a missing reason): ask only for what is missing, like `--form`."""
    out: list[ClientCommand] = []
    pack = rt.pack(refresh=False)
    for inc in prop.incomplete:
        meta = cat.require(inc.op)
        if not is_interactive():
            typer.echo(
                f"{inc.op}: incomplete ({'; '.join(inc.issues)}). Use `tt cmd {inc.op} --arg k=v`.", err=True
            )
            raise typer.Exit(2)
        fields = forms.fields_for(meta, pack)
        answers = forms.ask_form(fields, forms.QuestionaryPrompter(), given=inc.args)
        out.append(_build(rt, cat, meta, answers, mode=mode))
    return out


def _build(
    rt: Runtime,
    cat: Catalogue,
    meta: OpMeta,
    answers: dict[str, Any],
    *,
    mode: str,
    behalf: str | None = None,
) -> ClientCommand:
    try:
        return forms.build_command(
            meta, answers, catalogue=cat, pack=rt.pack(refresh=False), actor=rt.cfg.handle, now=now(),
            role_mode=current_mode(rt), mode=mode, on_behalf_of=behalf,
        )  # fmt: skip
    except forms.FormError as exc:
        typer.echo(f"{meta.op}: invalid arguments", err=True)
        for i in exc.issues:
            typer.echo(f"  - {i}", err=True)
        raise typer.Exit(2) from exc


def register_commands(app: typer.Typer) -> None:
    """Add the Phase 14 commands to the `tt` app (one call from main.py)."""
    original_send = next(
        (
            c.callback
            for c in app.registered_commands
            if c.name == "send" or (c.callback and c.callback.__name__ == "send_cmd")
        ),
        None,
    )
    app.registered_commands = [
        c for c in app.registered_commands if not (c.callback and c.callback.__name__ == "send_cmd")
    ]

    @app.command("role")
    def role_cmd(
        mode: Annotated[
            str | None, typer.Argument(help="reporter | manager | scrum | coach | pm | admin")
        ] = None,
    ) -> None:
        """Show or switch the role mode (a lens: only the modes the server allows you are offered)."""
        rt = load_runtime()
        allowed = _role_cache(rt).refresh(rt.api)
        cur = current_mode(rt)
        if mode is None:
            for m in allowed:
                typer.echo(f"{'*' if m == cur else ' '} {m:<13} {roles.describe(m)}")
            return
        try:
            chosen = roles.resolve_mode(mode, allowed)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        set_config_role(chosen)
        typer.echo(f"role: {roles.label(chosen)}")
        cat = _cat_cache(rt).load()[0]
        if cat is not None:
            s = roles.surface(chosen, cat)
            if s.first:
                typer.echo("first verbs: " + ", ".join(o.op for o in s.first[:8]))

    @app.command("ops")
    def ops_cmd(
        all_: Annotated[bool, typer.Option("--all", help="Include ops the current role mode hides")] = False,
        role: Annotated[str | None, typer.Option("--role", help="Preview another role mode")] = None,
        as_json: AsJson = False,
        import_file: Annotated[
            Path | None, typer.Option("--import", help="Load a catalogue JSON file (offline/air-gapped)")
        ] = None,
        refresh: Annotated[bool, typer.Option("--refresh")] = False,
    ) -> None:
        """List the command catalogue for the current role mode."""
        rt = load_runtime(require_login=False)
        if import_file:
            cat = _cat_cache(rt).import_file(import_file)
            typer.echo(f"imported {len(cat)} ops")
        else:
            cat = _cat_cache(rt).refresh(rt.api, force=refresh) or _catalogue(rt)
        mode = roles.normalise(role) if role else current_mode(rt)
        s = roles.surface(mode, cat)
        ops = s.reachable if all_ else s.visible
        if as_json:
            typer.echo(json.dumps([o.to_doc() for o in ops], indent=2))
            return
        table = Table(title=f"{roles.label(mode)} — {len(ops)} of {len(cat)} ops", box=None)
        for h in ("op", "group", "description", ""):
            table.add_column(h)
        pinned = {o.op for o in s.first}
        for o in ops:
            tags = ("!" if o.destructive else "") + ("p" if o.preview else "")
            table.add_row(
                ("★ " if o.op in pinned else "  ") + o.op, o.group, o.description, f"[dim]{tags}[/dim]"
            )
        console.print(table)
        console.print(
            "[dim]★ first for this role · ! destructive (needs --force with --yes) · p has an effect preview[/dim]"
        )

    @app.command("do")
    def do_cmd(
        text: Annotated[list[str], typer.Argument(help='e.g. tt do "merge WEB-101 and WEB-105"')],
        yes: Yes = False,
        force: Force = False,
        no_send: NoSend = False,
        as_json: AsJson = False,
        no_llm: Annotated[bool, typer.Option("--no-llm", help="Deterministic parser only")] = False,
        also: Annotated[
            str | None, typer.Option("--and", help="Also capture this line in the same batch")
        ] = None,
        for_: ForOpt = None,
    ) -> None:
        """Assisted: free text -> command card with an Effect preview -> confirm -> send."""
        rt = load_runtime()
        cat = _catalogue(rt)
        pack = rt.pack()
        role = current_mode(rt)
        line = " ".join(text)
        prop = propose(
            line, cat=cat, pack=pack, actor=rt.cfg.handle, now=now(), tz=rt.cfg.time_zone, role_mode=role,
            translator=None if no_llm else _translator(rt),
        )  # fmt: skip
        extra = _incomplete_to_commands(rt, cat, prop, "assisted") if prop.incomplete else []
        commands = [*prop.commands, *extra]
        if for_:
            commands = [c.model_copy(update={"on_behalf_of": for_}) for c in commands]
        for n in prop.notes:
            typer.echo(f"  note: {n}")
        if not commands:
            typer.echo(
                "I could not turn that into a command. Try `tt ops` for the verbs, or `tt cmd <op> --form`.",
                err=True,
            )
            raise typer.Exit(2)
        messages = _capture_messages(rt, also, pack) if also else None
        run_commands(
            rt, cat, commands, messages=messages, mode="assisted", yes=yes, force=force, no_send=no_send,
            as_json=as_json, source=f"{prop.source}",
        )  # fmt: skip

    @app.command("cmd")
    def cmd_cmd(
        op: Annotated[str, typer.Argument(help="e.g. item.merge")],
        arg: Annotated[
            list[str] | None, typer.Option("--arg", "-a", help="key=value (lists: a,b,c; objects: JSON)")
        ] = None,
        form: Annotated[bool, typer.Option("--form", help="Ask for the arguments one by one")] = False,
        yes: Yes = False,
        force: Force = False,
        no_send: NoSend = False,
        as_json: AsJson = False,
        for_: ForOpt = None,
    ) -> None:
        """Manual: send one op with explicit arguments, or fill a form generated from the op's JSON Schema."""
        rt = load_runtime()
        cat = _catalogue(rt)
        meta = cat.get(op)
        if meta is None:
            near = [n for n in cat.names if op.split(".")[0] in n][:6]
            typer.echo(
                f"unknown op {op!r}."
                + (f" Did you mean: {', '.join(near)}?" if near else " See `tt ops --all`."),
                err=True,
            )
            raise typer.Exit(2)
        try:
            given: dict[str, Any] = dict(forms.parse_kv(arg or []))
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        fields = forms.fields_for(meta, rt.pack(refresh=False))
        if form:
            if not is_interactive():
                typer.echo("--form needs a terminal; pass --arg key=value instead", err=True)
                raise typer.Exit(2)
            given = forms.ask_form(fields, forms.QuestionaryPrompter(), given=given)
        else:
            lacking = forms.missing(fields, given)
            if lacking:
                typer.echo(
                    f"{op} needs: {', '.join(lacking)}  (use --arg {lacking[0]}=… or --form)", err=True
                )
                raise typer.Exit(2)
        cmd = _build(rt, cat, meta, given, mode="manual", behalf=for_)
        run_commands(rt, cat, [cmd], mode="manual", yes=yes, force=force, no_send=no_send, as_json=as_json)

    @app.command("preview")
    def preview_cmd(ref: str) -> None:
        """Show the Effect preview of a saved outbox entry without sending it (never writes on the server)."""
        rt = load_runtime()
        cat = _catalogue(rt)
        entry = rt.outbox.find(ref)
        doc = rt.outbox.load(entry)
        cmds = [ClientCommand.model_validate(c) for c in doc.get("commands", [])]
        if not cmds:
            typer.echo("this entry has no commands (capture messages have no confirm step; use `tt send`).")
            return
        show_outcomes(preview_commands(rt.api, cat, cmds), cat, current_mode(rt))

    @app.command("send")
    def send_cmd(
        ref: str,
        jira: Annotated[bool, typer.Option("--jira")] = False,
        yes: Yes = False,
        force: Force = False,
    ) -> None:
        """Validate an outbox file and push it. Entries with commands are previewed/confirmed first (--yes; destructive: --force)."""
        rt = load_runtime()
        entry = rt.outbox.find(ref)
        doc = rt.outbox.load(entry)
        if not doc.get("commands"):
            if original_send is not None:
                original_send(ref, jira)
                return
        try:
            rt.outbox.validate(doc)
        except ValueError as exc:
            typer.echo(f"invalid ClientBatch: {exc}", err=True)
            raise typer.Exit(2) from exc
        cat = _catalogue(rt)
        cmds = apply_force(cat, [ClientCommand.model_validate(c) for c in doc["commands"]], force)
        doc["commands"] = [c.model_dump(mode="json", exclude_none=True) for c in cmds]
        entry.path.write_text(json.dumps(doc, indent=2, default=str))
        outcomes = preview_commands(rt.api, cat, cmds)
        show_outcomes(outcomes, cat, current_mode(rt))
        if any(o.rejected for o in outcomes):
            typer.echo("nothing was sent: the server would reject a command (see above).", err=True)
            raise typer.Exit(1)
        deliver_entry(rt, cat, entry, yes=yes, force=force)

    @app.command("queue")
    def queue_cmd(
        accept_defaults: Annotated[
            bool, typer.Option("--accept-defaults", help="Resolve every row with its default choice")
        ] = False,
        yes: Yes = False,
    ) -> None:
        """My review queue: resolve rows inline with the suggested default."""
        rt = load_runtime()
        try:
            rows = rt.api.review_queue()
        except (Unreachable, ApiError) as exc:
            typer.echo(f"cannot read the queue: {exc}", err=True)
            raise typer.Exit(1) from exc
        if not rows:
            typer.echo("review queue is empty")
            return
        cat = _catalogue(rt)
        picks: list[tuple[str, dict[str, Any]]] = []
        for row in (ReviewRow.from_api(r) for r in rows):
            typer.echo(row.line(now()))
            if row.choice is None:
                continue  # nothing suggested: the web console is the place to decide
            if accept_defaults or yes:
                picks.append((row.ref, row.choice))
            elif is_interactive():
                answer = typer.prompt("  Enter = accept, s = skip", default="", show_default=False)
                if answer.strip().lower() not in ("s", "skip"):
                    picks.append((row.ref, row.choice))
        if not picks:
            if not (accept_defaults or yes or is_interactive()):
                typer.echo(
                    "resolve with: tt queue --accept-defaults (or run it in a terminal and press Enter per row)"
                )
            return
        meta = cat.require("review.resolve")
        mode = "manual"
        cmds = [_build(rt, cat, meta, {"row": ref, "choice": ch}, mode=mode) for ref, ch in picks]
        run_commands(rt, cat, cmds, mode=mode, yes=True)

    team_app = typer.Typer(help="Your teams", no_args_is_help=True)

    @team_app.command("now")
    def team_now_cmd() -> None:
        """What my reports are working on right now."""
        rt = load_runtime()
        try:
            acts = rt.api.team_now()
        except (Unreachable, ApiError) as exc:
            typer.echo(f"cannot read team activity: {exc}", err=True)
            raise typer.Exit(1) from exc
        if not acts:
            typer.echo("nobody on your teams has an active task")
            return
        for line in (TeamNowRow.from_api(a).line(now()) for a in acts):
            typer.echo(line)

    app.add_typer(team_app, name="team")

    @app.command("approve")
    def approve_cmd(
        approval: Annotated[
            str | None, typer.Argument(help="approval id (default: pick from pending)")
        ] = None,
        comment: Annotated[str | None, typer.Option("--comment", "-c")] = None,
        reject: Annotated[bool, typer.Option("--reject", help="Reject instead of approve")] = False,
        yes: Yes = False,
        force: Force = False,
    ) -> None:
        """Approve (or --reject) a pending approval."""
        rt = load_runtime()
        cat = _catalogue(rt)
        pack = rt.pack()
        pending = pack.pending_approvals if pack else []
        if approval is None:
            if not pending:
                typer.echo("no pending approvals")
                return
            for n, a in enumerate(pending, 1):
                where = f"  [{a['project_key']}]" if a.get("project_key") else ""
                who = f"  {a['requested_by']}" if a.get("requested_by") else ""
                typer.echo(f"{n}  {a.get('title') or a.get('id')}{where}{who}")
            typer.echo("decide with: tt approve <number>  (or --reject)")
            if not is_interactive():
                raise typer.Exit(0)
            approval = typer.prompt("approve which number?")
        if approval.isdigit():  # the number printed by `tt approve`; ids stay available for scripts
            n = int(approval)
            if not 1 <= n <= len(pending):
                typer.echo(f"no pending approval number {n}; run `tt approve` to list them", err=True)
                raise typer.Exit(2)
            approval = str(pending[n - 1]["id"])
        args: dict[str, Any] = {
            "approval": approval if len(approval) > 8 else approval.upper(),
            "decision": "rejected" if reject else "approved",
        }
        if comment:
            args["comment"] = comment
        run_commands(
            rt,
            cat,
            [_build(rt, cat, cat.require("approval.decide"), args, mode="manual")],
            yes=yes,
            force=force,
        )

    @app.command("waive")
    def waive_cmd(
        violation: str,
        reason: Annotated[
            str | None, typer.Option("--reason", "-r", help="Why it is waived (required)")
        ] = None,
        yes: Yes = False,
        force: Force = False,
    ) -> None:
        """Waive a violation (a reason is required and recorded)."""
        rt = load_runtime()
        cat = _catalogue(rt)
        if not reason:
            if not is_interactive():
                typer.echo("a reason is required: --reason '…'", err=True)
                raise typer.Exit(2)
            reason = typer.prompt("reason")
        cmd = _build(
            rt,
            cat,
            cat.require("violation.waive"),
            {"id": violation.upper(), "reason": reason},
            mode="manual",
        )
        run_commands(rt, cat, [cmd], yes=yes, force=force)


def _capture_messages(rt: Runtime, line: str, pack: Any) -> list[ClientMessage]:
    from tt_client.parser.llm import translate_line

    t = translate_line(line, rt.parse_ctx(), pack=pack, translator=rt.translator())
    return list(t.batch.messages)


__all__ = ["META", "adopt_gateway_commands", "new_command", "register_commands", "run_commands"]
