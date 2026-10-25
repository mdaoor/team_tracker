"""`tt` — the reporter CLI. Free text is the default command: `tt "fix login, took 2h"`."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from tt_client import form as tform
from tt_client.api import ApiError, Unreachable
from tt_client.config import ClientConfig
from tt_client.feedback import diff_batches
from tt_client.login import LoginError, browser_login, check_server, ldap_token
from tt_client.outbox import Entry, sync
from tt_client.parser.llm import GatewayTranslator, batch_review, translate_line
from tt_client.preview import preview_dict
from tt_schema.client import ClientBatch, ClientMessage
from typer.core import TyperGroup

from tt_cli import personal as personal_cli
from tt_cli import render
from tt_cli.runtime import Runtime, load_runtime, now


class DefaultGroup(TyperGroup):
    """Unknown first word => free text for the `say` command."""

    def resolve_command(self, ctx: Any, args: list[str]) -> Any:
        if args and not args[0].startswith("-") and self.get_command(ctx, args[0]) is None:
            args = ["say", *args]
        return super().resolve_command(ctx, args)


app = typer.Typer(
    cls=DefaultGroup,
    help="Team Tracker — type one line, move on.",
    no_args_is_help=True,
    add_completion=False,
)

Yes = Annotated[bool, typer.Option("--yes", "-y", help="Send without asking")]
NoSend = Annotated[bool, typer.Option("--no-send", help="Write the outbox file only")]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Print the batch; write and send nothing")]
AsJson = Annotated[bool, typer.Option("--json", help="Print the ClientBatch JSON")]
JiraOpt = Annotated[bool, typer.Option("--jira", help="Also create/update the Jira issue (server side)")]
ProjectOpt = Annotated[str | None, typer.Option("--project", "-p", help="Project key/alias")]
EditOpt = Annotated[bool, typer.Option("--edit", "-e", help="Open the outbox file in $EDITOR before sending")]


# ------------------------------------------------------------------ core flow


def _set_jira(batch: ClientBatch, pack_data: dict[str, Any] | None, force: bool) -> None:
    jira = {p["key"]: p for p in (pack_data or {}).get("projects", []) if p.get("jira")}
    for m in batch.messages:
        proj = (m.project or "").upper()
        cfg = jira.get(proj)
        if force or (cfg and cfg.get("jira_auto_send")):
            m.jira = {"project": (cfg or {}).get("jira") or m.project, "send": True}


def _deliver(rt: Runtime, entries: list[Entry]) -> None:
    report = sync(rt.api, rt.outbox, only=entries)
    if report.offline:
        typer.echo(f"offline — queued {report.kept} batch(es); `tt sync` will deliver them (exactly once).")
        return
    if report.quarantined:
        typer.echo(
            "the server rejected the file (schema violation) — it was quarantined; this is a client bug.",
            err=True,
        )
    for r in report.results or []:
        flag = {
            "applied": "✓",
            "deduplicated": "✓ (already had it)",
            "applied_with_warnings": "✓ with warnings",
        }.get(r["status"], "✗")
        item = r.get("item") or {}
        extra = (
            f" jira:{r['jira']['status']}" + (f" {r['jira']['key']}" if r["jira"].get("key") else "")
            if r.get("jira")
            else ""
        )
        typer.echo(f"{flag} {item.get('key', '')} {item.get('title', '')}{extra}".rstrip())
        for w in r.get("warnings", []):
            typer.echo(f"   ! {w}")
        for rid in r.get("review_item_ids", []):
            typer.echo(f"   → sent to your manager's review queue ({rid[:8]})")
        if r["status"] == "failed":
            typer.echo(f"   error: {r.get('error_code')}", err=True)
    if report.failed_messages:
        raise typer.Exit(1)
    if rt.cfg.feedback:
        _flush_feedback(rt)


def _flush_feedback(rt: Runtime) -> None:
    items = rt.feedback.drain()
    if not items:
        return
    try:
        rt.api.post_feedback(items)
    except (Unreachable, ApiError):
        rt.feedback.append(items)


def _open_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    subprocess.run([*shlex.split(editor), str(path)], check=True)  # noqa: S603


def _edit_entry(rt: Runtime, entry: Entry) -> None:
    before = rt.outbox.load(entry)
    _open_editor(entry.path)
    try:
        after = rt.outbox.load(entry)
        rt.outbox.validate(after)
    except ValueError as exc:
        typer.echo(f"invalid ClientBatch after editing: {exc}", err=True)
        raise typer.Exit(2) from exc
    if rt.cfg.feedback:
        rt.feedback.append(diff_batches(before, after))
    rt.outbox.db.execute(
        "UPDATE pending SET batch_json = ? WHERE client_id = ?", (json.dumps(after), entry.batch_id)
    )
    rt.outbox.db.commit()


def _capture(
    rt: Runtime,
    line: str,
    *,
    mode: str = "assisted",
    jira: bool = False,
    yes: bool = False,
    no_send: bool = False,
    dry_run: bool = False,
    as_json: bool = False,
    edit: bool = False,
) -> None:
    pack = rt.pack()
    t = translate_line(line, rt.parse_ctx(), pack=pack, translator=rt.translator(), mode=mode)
    if not t.batch.messages:
        typer.echo("nothing to send", err=True)
        raise typer.Exit(2)
    batch = t.batch.model_copy(update={"workspace": rt.cfg.workspace})
    _set_jira(batch, pack.data if pack else None, jira)
    if as_json or dry_run:
        typer.echo(json.dumps(batch.model_dump(mode="json", exclude_none=True), indent=2))
        if dry_run:
            return
    else:
        render.show(batch.messages, pack, fallback=t.source == "structured" and bool(rt.cfg.gateway_url))
        for n in t.notes:
            typer.echo(f"  note: {n}")
    _finish(rt, batch, pack, yes=yes, no_send=no_send, edit=edit)


def _finish(rt: Runtime, batch: ClientBatch, pack: Any, *, yes: bool, no_send: bool, edit: bool) -> None:
    entry = rt.outbox.write(
        batch.model_dump(mode="json", exclude_none=True), preview_dict(batch.messages, pack)
    )
    if edit:
        _edit_entry(rt, entry)
    if no_send:
        typer.echo(f"saved {entry.path}  (tt edit {entry.batch_id[:8]} · tt send {entry.batch_id[:8]})")
        return
    if not yes and sys.stdin.isatty() and not typer.confirm("Send?", default=True):
        typer.echo(f"kept in the outbox: {entry.path}")
        return
    _deliver(rt, [entry])


# ------------------------------------------------------------------ commands


@app.command("say")
def say(
    text: Annotated[list[str], typer.Argument(help="One plain-English line")],
    yes: Yes = False,
    no_send: NoSend = False,
    dry_run: DryRun = False,
    as_json: AsJson = False,
    jira: JiraOpt = False,
    edit: EditOpt = False,
    project: ProjectOpt = None,
) -> None:
    """Assisted mode: free text -> structured batch -> outbox file -> server."""
    line = " ".join(text) + (f" p:{project}" if project else "")
    _capture(
        load_runtime(), line, yes=yes, no_send=no_send, dry_run=dry_run, as_json=as_json, jira=jira, edit=edit
    )


def _verb_command(verb: str, template: str) -> None:
    def cmd(
        words: Annotated[list[str], typer.Argument(help="item / details")],
        project: ProjectOpt = None,
        yes: Yes = False,
        no_send: NoSend = False,
        dry_run: DryRun = False,
        as_json: AsJson = False,
        jira: JiraOpt = False,
        edit: EditOpt = False,
    ) -> None:
        line = template.format(" ".join(words)) + (f" p:{project}" if project else "")
        _capture(
            load_runtime(),
            line,
            yes=yes,
            no_send=no_send,
            dry_run=dry_run,
            as_json=as_json,
            jira=jira,
            edit=edit,
        )

    cmd.__doc__ = f"`tt {verb} ...` is shorthand for the line “{template.format('...')}”."
    app.command(verb)(cmd)


for _verb, _tpl in {
    "doing": "doing {}",
    "done": "done {}",
    "pause": "pause {}",
    "resume": "resume {}",
    "blocked": "blocked {}",
    "note": "note: {}",
    "time": "took {}",
    "plan": "plan: {}",
    "est": "est {}",
    "new": "new task: {}",
}.items():
    _verb_command(_verb, _tpl)


@app.command("form")
def form_cmd(
    from_ref: Annotated[
        str | None, typer.Option("--from", help="Open an existing outbox file in the form")
    ] = None,
    answers: Annotated[
        Path | None, typer.Option("--answers", help="JSON file with answers (non-interactive)")
    ] = None,
    yes: Yes = False,
    no_send: NoSend = False,
) -> None:
    """Manual mode: fill a form whose choices come from the ContextPack; mandatory fields are enforced."""
    rt = load_runtime()
    pack = rt.pack()
    if pack is None:
        typer.echo("no ContextPack yet — run `tt pack --refresh` or `tt pack --import FILE`", err=True)
        raise typer.Exit(2)
    given: dict[str, Any] = {}
    if from_ref:
        entry = rt.outbox.find(from_ref)
        given = tform.message_to_answers(ClientMessage.model_validate(rt.outbox.load(entry)["messages"][0]))
    if answers:
        given.update(json.loads(answers.read_text()))
    elif sys.stdin.isatty():
        given = _ask_form(pack, given)
    missing = tform.missing_required(given, pack)
    if missing:
        typer.echo(f"missing mandatory field(s): {', '.join(missing)}", err=True)
        raise typer.Exit(2)
    msg = tform.build_message(
        given, actor=rt.cfg.handle, now=now(), tz=rt.cfg.time_zone, hours_per_day=rt.cfg.hours_per_day
    )
    batch = tform.to_batch([msg], rt.cfg.workspace)
    _set_jira(batch, pack.data, bool(given.get("jira")))
    render.show(batch.messages, pack)
    _finish(rt, batch, pack, yes=yes, no_send=no_send, edit=False)


def _ask_form(pack: Any, given: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - interactive
    import questionary

    ch = tform.choices(pack)
    out = dict(given)
    out["action"] = questionary.select("Action", choices=ch["action"], default=out.get("action")).ask()
    out["item"] = questionary.autocomplete(
        "Item (pick or type a new title)", choices=ch["item"], default=out.get("item") or ""
    ).ask()
    out["project"] = questionary.select("Project", choices=ch["project"], default=out.get("project")).ask()
    if out["action"] == "create":
        out["item_type"] = questionary.select("Type", choices=ch["item_type"]).ask()
    if out["action"] in ("log_time", "done"):
        out["minutes"] = questionary.text("Time (e.g. 1h30m)", default=str(out.get("minutes") or "")).ask()
    out["tags"] = questionary.checkbox("Tags", choices=ch["tags"]).ask() + [
        t.strip("# ") for t in (questionary.text("More tags (space separated)").ask() or "").split() if t
    ]
    out["priority"] = questionary.select("Priority", choices=["", *ch["priority"]]).ask() or None
    out["planned_end"] = questionary.text(
        "Plan (e.g. friday 5pm)", default=out.get("planned_end") or ""
    ).ask()
    out["estimate"] = questionary.text("Estimate (e.g. 3d)", default=out.get("estimate") or "").ask()
    out["note"] = questionary.text("Note", default=out.get("note") or "").ask()
    return out


@app.command("edit")
def edit_cmd(ref: str) -> None:
    """Open an outbox file in $EDITOR, re-validate it, record corrections."""
    rt = load_runtime()
    _edit_entry(rt, rt.outbox.find(ref))
    typer.echo("ok — `tt send` when ready")


@app.command("send")
def send_cmd(ref: str, jira: JiraOpt = False) -> None:
    """Validate an outbox file (or any ClientBatch JSON file) and push it to the server."""
    rt = load_runtime()
    entry = rt.outbox.find(ref)
    doc = rt.outbox.load(entry)
    try:
        batch = rt.outbox.validate(doc)
    except ValueError as exc:
        typer.echo(f"invalid ClientBatch: {exc}", err=True)
        raise typer.Exit(2) from exc
    if jira:
        _set_jira(batch, None, True)
        entry.path.write_text(json.dumps(batch.model_dump(mode="json", exclude_none=True), indent=2))
    _deliver(rt, [entry])


@app.command("outbox")
def outbox_cmd(sent: Annotated[bool, typer.Option("--sent")] = False) -> None:
    """List pending (or sent) outbox files."""
    rt = load_runtime(require_login=False)
    if sent:
        for p in sorted(rt.outbox.sent_dir.glob("*.json")):
            typer.echo(p.name)
        return
    pending = rt.outbox.pending()
    for e in pending:
        err = f"  ({e.attempts}x: {e.last_error[:60]})" if e.last_error else ""
        typer.echo(f"{e.batch_id[:8]}  {e.created_at[:19]}  {e.path.name}{err}")
    if not pending:
        typer.echo("outbox is empty")


@app.command("sync")
def sync_cmd(
    assist: Annotated[
        bool, typer.Option("--assist", help="Let the gateway merge/clean pending batches first")
    ] = False,
    yes: Yes = False,
) -> None:
    """Deliver everything pending, oldest first."""
    rt = load_runtime()
    if assist:
        gw = rt.translator()
        if not isinstance(gw, GatewayTranslator):
            typer.echo("--assist needs [llm] gateway_url in the config", err=True)
            raise typer.Exit(2)
        entries = rt.outbox.pending()
        cleaned = batch_review(gw, [rt.outbox.load(e) for e in entries])
        if cleaned is None:
            typer.echo("gateway unavailable — sending as is")
        elif len(cleaned) != len(entries):
            typer.echo(f"gateway proposes {len(entries)} → {len(cleaned)} batch(es)")
            if yes or typer.confirm("Apply?", default=False):
                for e in entries:
                    rt.outbox.discard(e)
                for b in cleaned:
                    rt.outbox.write(b)
    _deliver(rt, rt.outbox.pending())


@app.command("now")
def now_cmd() -> None:
    """What am I tracking right now?"""
    rt = load_runtime()
    cache = rt.data / "now.json"
    try:
        acts = rt.api.active_activities()
        cache.write_text(json.dumps(acts))
        suffix = ""
    except Unreachable:
        acts = json.loads(cache.read_text()) if cache.exists() else []
        suffix = " (offline, last known)"
    if not acts:
        typer.echo("nothing running" + suffix)
    for a in acts:
        started = a["started_at"][:16].replace("T", " ")
        typer.echo(f"▶ {a['item_key']} {a['item_title']}  [{a['project_key']}] since {started}{suffix}")


@app.command("today")
def today_cmd(confirm: Annotated[bool, typer.Option("--confirm")] = False) -> None:
    """Show today's daily-log draft; `--confirm` confirms it."""
    rt = load_runtime()
    day = now().astimezone(__import__("zoneinfo").ZoneInfo(rt.cfg.time_zone)).date().isoformat()
    logs = rt.api.daily_logs(day)
    if not logs:
        typer.echo("no daily log yet (it is drafted at 17:30 or when you log work)")
        return
    log = logs[0]
    typer.echo(f"{log['date']}  {log['status']}  {log['minutes_total'] / 60:.1f}h\n{log['summary']}")
    if log["blockers"]:
        typer.echo(f"blockers: {log['blockers']}")
    if confirm:
        rt.api.confirm_daily_log(log["id"])
        typer.echo("confirmed ✓")


@app.command("pack")
def pack_cmd(
    refresh: Annotated[bool, typer.Option("--refresh")] = False,
    import_: Annotated[
        Path | None, typer.Option("--import", help="Load a ContextPack JSON file (air-gapped)")
    ] = None,
    export: Annotated[Path | None, typer.Option("--export")] = None,
) -> None:
    """Refresh, import or export the ContextPack."""
    rt = load_runtime()
    if import_:
        imported = rt.cache.import_file(import_)
        typer.echo(f"imported: {len(imported.project_keys)} projects, {len(imported.open_items)} open items")
    elif export:
        rt.cache.export_file(export)
        typer.echo(f"exported to {export}")
    else:
        p = rt.cache.refresh(rt.api, force=refresh)
        if p is None:
            typer.echo("no pack available (server unreachable and nothing cached)", err=True)
            raise typer.Exit(1)
        typer.echo(
            f"{p.data['actor']}@{p.data['workspace']}: {len(p.project_keys)} projects, "
            f"{len(p.open_items)} open items, "
            f"{len(p.tags)} tags"
        )


@app.command("login")
def login(
    url: Annotated[str, typer.Option(help="Server URL (https://...)")],
    workspace: Annotated[str, typer.Option(help="Workspace slug")],
    handle: Annotated[str | None, typer.Option(help="Your @handle (looked up when signing in)")] = None,
    token: Annotated[str | None, typer.Option(help="Personal API token (tt_...)")] = None,
    browser: Annotated[bool, typer.Option("--browser", help="Sign in through the web console")] = False,
    ldap_user: Annotated[
        str | None, typer.Option("--ldap-user", help="Active Directory account (DOMAIN\\user or UPN)")
    ] = None,
    ca_bundle: Annotated[
        str | None, typer.Option(help="PEM CA bundle for internal CAs (TT_CA_BUNDLE)")
    ] = None,
    gateway_url: Annotated[str | None, typer.Option(help="LLM gateway URL")] = None,
    time_zone: Annotated[str, typer.Option(help="IANA zone")] = "UTC",
) -> None:
    """Sign in and store server settings. The TLS chain is verified first and cannot be skipped."""
    cfg = ClientConfig.load()
    ca = ca_bundle or cfg.ca_bundle
    try:
        check_server(url, ca)
        if browser:
            import webbrowser

            typer.echo("Opening your browser to sign in ...")
            token, found = browser_login(url, workspace, open_url=webbrowser.open)
            handle = handle or found
        elif ldap_user:
            password = typer.prompt("Directory password", hide_input=True)
            token, found = ldap_token(url, workspace, ldap_user, password, ca)
            handle = handle or found
        elif token is None:
            token = typer.prompt("Paste your API token", hide_input=True)
    except LoginError as exc:
        typer.echo(f"login failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not handle:
        typer.echo("--handle is required when signing in with a token", err=True)
        raise typer.Exit(2)
    cfg.server_url, cfg.workspace, cfg.handle = (
        url,
        workspace,
        handle if handle.startswith("@") else "@" + handle,
    )
    cfg.token, cfg.ca_bundle, cfg.gateway_url, cfg.time_zone = (
        token or "",
        ca,
        gateway_url or cfg.gateway_url,
        time_zone,
    )
    path = cfg.save()
    typer.echo(f"saved {path} (mode 600)")


gateway_app = typer.Typer(help="The LLM gateway (sidecar for this laptop)", no_args_is_help=True)
app.add_typer(gateway_app, name="gateway")


@gateway_app.command("serve")
def gateway_serve(
    local_only: Annotated[bool, typer.Option("--local-only/--allow-cloud", help="Local models only")] = True,
    port: Annotated[int, typer.Option(help="Loopback port")] = 8700,
    config: Annotated[Path | None, typer.Option(help="llm_gateway.yaml")] = None,
) -> None:
    """Run the same gateway the team uses, bound to 127.0.0.1 with Ollama; then `tt login --gateway-url`."""
    try:
        import uvicorn
        from tt_llm.config import load_config
        from tt_llm.main import apply_local_only, build_app
    except ImportError:
        typer.echo("the gateway is not installed: pip install tt-llm-gateway", err=True)
        raise typer.Exit(1) from None
    cfg = load_config(config)
    if local_only:
        cfg = apply_local_only(cfg, "127.0.0.1")
    typer.echo(f"gateway on http://127.0.0.1:{port} (privacy_mode={cfg.privacy_mode})")
    uvicorn.run(build_app(cfg), host="127.0.0.1", port=port)


personal_cli.register(app)  # Phase 15: day/week/month/add/timer/cal/tray/recur/privacy/sent/erase-local/...

if __name__ == "__main__":
    app()
