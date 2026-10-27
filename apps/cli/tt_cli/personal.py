"""`tt day|week|month|add|timer|cal|tray|recur|item|privacy|sent|erase-local|eod|review|export|import|sidecar` - the personal tracker (Phase 15).

Everything here works on the local encrypted store; nothing touches the network except `tt cal connect|sync` (the calendar providers) and
the delivery that `tt tray send` and `tt eod --send-all` hand to the ordinary outbox (`tt sync` or the next `tt` send delivers it).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from tt_client.config import ClientConfig, data_dir
from tt_client.pack import PackCache
from tt_client.personal.model import Category, SharePolicy
from tt_client.personal.recurrence import zone
from tt_client.personal.send import NotSendable
from tt_client.personal.service import Env, Tracker, TrackerError
from tt_client.secrets import default_store

from tt_cli.runtime import now

AsJson = Annotated[bool, typer.Option("--json", help="Print JSON")]


def tracker() -> Tracker:
    """The personal tracker for this user. Works with no login: the server is optional (plan 15, principle 1)."""
    cfg = ClientConfig.load()
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    cache = PackCache(data / "pack.json")

    def pack() -> dict[str, Any] | None:
        loaded = cache.load()[0]
        return loaded.data if loaded else None

    cal = cfg.extra.get("calendar", {})
    return Tracker(
        Env(
            data_dir=data,
            secrets=default_store(data),
            tz=cfg.time_zone,
            workspace=cfg.workspace,
            actor=cfg.handle,
            email=cal.get("email"),
            clock=now,
            pack=pack,
            extra={"cfg": cfg},
        )
    )


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(1)


def _date(text: str | None, today: date) -> date:
    if not text:
        return today
    t = text.lower()
    if t in ("today", "tomorrow", "yesterday"):
        return today + timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[t])
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise typer.BadParameter(f"{text!r} is not YYYY-MM-DD, today, tomorrow or yesterday") from exc


def _h(minutes: int | None) -> str:
    if not minutes:
        return "-"
    h, m = divmod(int(minutes), 60)
    return f"{h} h" if h and not m else f"{h} h {m:02d}" if h else f"{m} min"


def short(item_id: str) -> str:
    """The random tail of the id (the head of a UUID v7 is a timestamp, so many ids share it)."""
    if "@" in item_id:
        master, day = item_id.rsplit("@", 1)
        return f"{master[-8:]}@{day}"
    return item_id[-8:]


def _clock(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M") if iso else "     "


def _row(r: dict[str, Any]) -> str:
    when = f"{_clock(r['start'])}-{_clock(r['end'])}" if r["start"] and not r["all_day"] else "  anytime  "
    marks = ("↻" if r["recurring"] else " ") + (
        "✓"
        if r["status"] == "done"
        else "⏸"
        if r["status"] == "skipped"
        else "▶"
        if r["timer"] == "running"
        else " "
    )
    extra = []
    if r["sent"]:
        extra.append("sent")
    if r["online_meeting_url"]:
        extra.append("join: " + r["online_meeting_url"])
    if r["from_calendar"]:
        extra.append(r["from_calendar"])
    return f"  {when}  {marks} {r['title']:<32.32} {_h(r['minutes_planned']):>8}  [{r['category']}]  {short(r['id'])}  {' · '.join(extra)}".rstrip()


def show_day(d: dict[str, Any]) -> None:
    typer.echo(
        f"{datetime.fromisoformat(d['date']).strftime('%a %d %b %Y')}   planned {_h(d['planned_min'])} of {_h(d['capacity_min'])} ({d['load_pct']} %)"
    )
    if d["timer"]:
        typer.echo(f"  ▶ timer running: {d['timer']['elapsed_min']} min")
    for r in [*d["timeline"], *d["anytime"]]:
        typer.echo(_row(r))
    for s in d.get("server_items", []):
        typer.echo(f"  {'(server)':^11}    {s.get('key', '')} {s.get('title', '')}")
    if not (d["timeline"] or d["anytime"]):
        typer.echo('  nothing planned: try `tt add "run 30m at 6pm"`')


def register(app: typer.Typer) -> None:
    @app.command("day")
    def day_cmd(
        when: Annotated[str | None, typer.Argument(help="YYYY-MM-DD, today, tomorrow")] = None,
        as_json: AsJson = False,
    ) -> None:
        """Today (or another day): meetings, planned items, the timer, and what is ready to send."""
        t = tracker()
        d = t.day(_date(when, t.today))
        if as_json:
            typer.echo(json.dumps(d, indent=2))
            return
        show_day(d)
        n = len(t.tray())
        if n:
            typer.echo(f"\n{n} ready to send: `tt tray`")

    @app.command("week")
    def week_cmd(when: Annotated[str | None, typer.Argument()] = None, as_json: AsJson = False) -> None:
        """The week of a day, with a capacity bar per day."""
        t = tracker()
        w = t.week(_date(when, t.today))
        if as_json:
            typer.echo(json.dumps(w, indent=2))
            return
        for d in w["days"]:
            bar = "█" * min(10, round(d["load_pct"] / 10)) + "·" * (10 - min(10, round(d["load_pct"] / 10)))
            typer.echo(
                f"{datetime.fromisoformat(d['date']).strftime('%a %d')}  {bar}  {_h(d['planned_min']):>8} / {_h(d['capacity_min']):<8}"
            )
            for r in [*d["timeline"], *d["anytime"]]:
                typer.echo("   " + _row(r).strip())
        tot = w["totals"]
        typer.echo("\n" + "   ".join(f"{c} {_h(v['planned'])}" for c, v in tot.items()))

    @app.command("month")
    def month_cmd(
        ym: Annotated[str | None, typer.Argument(help="YYYY-MM")] = None, as_json: AsJson = False
    ) -> None:
        """A calendar grid with dots per category and the month's hours."""
        t = tracker()
        year, month = (int(x) for x in ym.split("-")) if ym else (t.today.year, t.today.month)
        m = t.month(year, month)
        if as_json:
            typer.echo(json.dumps(m, indent=2))
            return
        typer.echo(f"{date(year, month, 1).strftime('%B %Y')}\n  Mo   Tu   We   Th   Fr   Sa   Su")
        sym = {"work": "w", "meeting": "m", "development": "d", "personal": "p"}
        for week in m["weeks"]:
            cells = []
            for iso in week:
                dd = date.fromisoformat(iso)
                dots = "".join(sym[c] for c in m["days"].get(iso, {}).get("dots", []))
                cells.append(f"{dd.day:>2}{dots:<3}" if dd.month == month else "     ")
            typer.echo("  " + " ".join(cells))
        h = m["totals_h"]
        typer.echo(
            f"\nhours: work {h['work']}  meeting {h['meeting']}  development {h['development']}  personal {h['personal']}"
        )
        sent = sum(s["sent"] for s in m["sparkline"])
        private = sum(s["private"] for s in m["sparkline"])
        typer.echo(f"sent {_h(sent)} · private {_h(private)}")

    @app.command("add")
    def add_cmd(
        line: Annotated[list[str], typer.Argument(help='One line: "run 30m at 6pm", "course: Rust ch.3 1h"')],
        preview: Annotated[bool, typer.Option("--preview", help="Show what would be created")] = False,
        as_json: AsJson = False,
    ) -> None:
        """Quick add to the personal planner (local; nothing is sent)."""
        t = tracker()
        text = " ".join(line)
        try:
            if preview:
                rows = t.preview(text)
                typer.echo(
                    json.dumps(rows, indent=2)
                    if as_json
                    else "\n".join(
                        f"{r['title']}  [{r['category']}]  {r.get('rrule_text') or r['start']}" for r in rows
                    )
                )
                return
            cfg: ClientConfig = t.env.extra["cfg"]
            assist = None
            if cfg.gateway_url:
                from tt_client.parser.personal_llm import fetch

                pk = t.env.pack() or {}
                assist = lambda ln: fetch(  # noqa: E731
                    cfg.gateway_url or "", ln, actor=cfg.handle or "@me", now=t.now.isoformat(), time_zone=cfg.time_zone,
                    projects=[{"key": p["key"], "name": p.get("name", "")} for p in pk.get("projects", [])],
                )  # fmt: skip
            items = t.add(text, assist=assist)
        except (TrackerError, ValueError) as exc:
            raise _fail(exc) from exc
        for i in items:
            when = (
                i.rrule
                and t.describe_rule(i)
                or (
                    i.start.astimezone(zone(t.env.tz)).strftime("%a %d %b %H:%M")
                    if i.start and not i.all_day
                    else "anytime"
                )
            )
            typer.echo(f"added {i.title}  [{i.category.value}]  {when}  {short(i.id)}")

    # ------------------------------------------------------------------ timer
    timer_app = typer.Typer(
        help="One timer at a time: starting another pauses the first", no_args_is_help=True
    )
    app.add_typer(timer_app, name="timer")

    @timer_app.command("start")
    def timer_start(
        ref: Annotated[str | None, typer.Argument(help="item id/prefix (see `tt day`)")] = None,
        title: Annotated[
            str | None, typer.Option("--title", "-t", help="start a new item with this title")
        ] = None,
    ) -> None:
        t = tracker()
        try:
            out = t.timer_start(t.find(ref) if ref else None, title=title)
        except (TrackerError, ValueError) as exc:
            raise _fail(exc) from exc
        typer.echo(f"▶ {out['title']}" + (" (paused the previous timer)" if out.get("paused") else ""))

    @timer_app.command("stop")
    def timer_stop() -> None:
        out = tracker().timer_stop()
        typer.echo("no timer" if out is None else f"■ {out['title']}  {_h(out['minutes'])}")

    @timer_app.command("status")
    def timer_status() -> None:
        out = tracker().timer_status()
        typer.echo("no timer running" if out is None else f"▶ {out['title']}  {_h(out['elapsed_min'])}")

    # ------------------------------------------------------------------ calendars
    cal_app = typer.Typer(
        help="Google Calendar and Outlook/Teams meetings -> ready-to-send tasks", no_args_is_help=True
    )
    app.add_typer(cal_app, name="cal")

    def providers(t: Tracker) -> dict[str, Any]:
        from tt_client.integrations.factory import build_providers

        cfg: ClientConfig = t.env.extra["cfg"]
        return build_providers(cfg.extra.get("calendar", {}), t.env.secrets)

    @cal_app.command("connect")
    def cal_connect(provider: Annotated[str, typer.Argument(help="google | microsoft")]) -> None:
        """Sign in in your browser (OAuth with PKCE). The token goes to the OS keychain."""
        from tt_client.integrations.base import ProviderError
        from tt_client.integrations.factory import connect

        t = tracker()
        provs = providers(t)
        if provider not in ("google", "microsoft"):
            raise typer.BadParameter("google or microsoft")
        if provider not in provs:
            key = (
                "TT_GOOGLE_CLIENT_ID / [calendar] google_client_id"
                if provider == "google"
                else "TT_MS_CLIENT_ID / [calendar] microsoft_client_id"
            )
            typer.echo(
                f"no client id for {provider}: set {key} (see docs/client/calendar-integrations.md)", err=True
            )
            raise typer.Exit(2)
        typer.echo("Opening your browser to sign in ...")
        try:
            connect(provs[provider])
            report = t.calendar(provs).sync(provider)
        except ProviderError as exc:
            raise _fail(exc) from exc
        typer.echo(
            f"connected {provider}; {report.added} meetings added"
            + (f" (hidden: {sum(report.skipped.values())})" if report.skipped else "")
        )

    @cal_app.command("sync")
    def cal_sync(
        provider: Annotated[str | None, typer.Option("--provider", "-p")] = None,
        full: Annotated[bool, typer.Option("--full", help="ignore the stored cursor")] = False,
        watch: Annotated[
            bool, typer.Option("--watch", help="keep syncing every few minutes (Ctrl-C to stop)")
        ] = False,
    ) -> None:
        """Pull the calendars now (or keep doing it with --watch)."""
        t = tracker()
        provs = providers(t)
        if not provs:
            typer.echo("no calendar configured: `tt cal connect google|microsoft`", err=True)
            raise typer.Exit(2)
        sync = t.calendar(provs)
        for name in [provider] if provider else list(provs):
            r = sync.sync(name, full=full)
            line = f"{name}: +{r.added} ~{r.updated} -{r.removed}" + (
                f"  hidden {dict(r.skipped)}" if r.skipped else ""
            )
            typer.echo(line if not r.error else f"{name}: sync failed: {r.error}")
        if watch:
            import time as _t

            from tt_client.integrations.sync import Scheduler

            sched = Scheduler(sync)
            while True:  # pragma: no cover - interactive
                for name, why, r in sched.tick():
                    typer.echo(
                        f"{name} ({why}): +{r.added} ~{r.updated} -{r.removed}"
                        + (f" ERROR {r.error}" if r.error else "")
                    )
                _t.sleep(30)

    @cal_app.command("status")
    def cal_status() -> None:
        t = tracker()
        provs = providers(t)
        names = sorted(
            {*provs, *(n.split(":", 1)[1] for n in t.env.secrets.names() if n.startswith("oauth:"))}
        )
        if not names:
            typer.echo("no calendars connected")
        sync = t.calendar(provs)
        for n in names:
            st = sync.status(n)
            typer.echo(
                f"{n}: last ok {st['last_ok'] or 'never'}"
                + (f"  ERROR: {st['error']}" if st["error"] else "")
            )

    @cal_app.command("disconnect")
    def cal_disconnect(
        provider: str, keep_meetings: Annotated[bool, typer.Option("--keep-meetings")] = False
    ) -> None:
        """Forget the token and sync state; unsent meetings from that calendar are removed unless --keep-meetings."""
        from tt_client.integrations.factory import disconnect

        t = tracker()
        provs = providers(t)
        if provider in provs:
            disconnect(provs[provider])
        else:
            t.env.secrets.delete(f"oauth:{provider}")
        removed = t.calendar(provs).disconnect(provider, delete_items=not keep_meetings)
        typer.echo(f"disconnected {provider}; {removed} meeting(s) removed")

    # ------------------------------------------------------------------ tray
    tray_app = typer.Typer(
        help="Ready to send: finished meetings and done work, one tap each", invoke_without_command=True
    )
    app.add_typer(tray_app, name="tray")

    def _entry(t: Tracker, ref: str) -> str:
        entries = t.tray()
        if ref.isdigit():
            n = int(ref)
            if not 1 <= n <= len(entries):
                raise TrackerError(f"no tray entry {n} (there are {len(entries)})")
            return entries[n - 1].item.id
        return t.find(ref)

    @tray_app.callback()
    def tray_list(ctx: typer.Context, as_json: AsJson = False) -> None:
        if ctx.invoked_subcommand:
            return
        t = tracker()
        rows = t.tray_rows()
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
            return
        if not rows:
            typer.echo("nothing to send")
            return
        for n, r in enumerate(rows, 1):
            s = r["suggestion"]
            cue = f"{int(s['confidence'] * 100)} % {s['cue']}" if s["project"] else "ask"
            typer.echo(f'{n}. {r["label"]}   ({cue})   "{r["note"]}"')
        typer.echo("\n`tt tray send <n>` · `tt tray skip <n>` · `tt tray notwork <n>`")

    @tray_app.command("send")
    def tray_send(
        ref: Annotated[str, typer.Argument(help="tray number or item id")],
        project: Annotated[str | None, typer.Option("--project", "-p")] = None,
        note: Annotated[str | None, typer.Option("--note")] = None,
        minutes: Annotated[int | None, typer.Option("--minutes")] = None,
        yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    ) -> None:
        """Queue the item as log_time (+ note). Delivered by the next `tt sync`; unsend within 10 minutes with `tt sent unsend`."""
        t = tracker()
        try:
            res = t.send(_entry(t, ref), project=project, note=note, minutes=minutes)
        except (TrackerError, NotSendable) as exc:
            raise _fail(exc) from exc
        typer.echo(
            f"queued {_h(res['minutes'])} → {res['project']}"
            + ("  #development" if "development" in res["tags"] else "")
        )
        _deliver_if_logged_in(t, yes)

    @tray_app.command("skip")
    def tray_skip(ref: str) -> None:
        t = tracker()
        try:
            t.skip(_entry(t, ref))
        except TrackerError as exc:
            raise _fail(exc) from exc
        typer.echo("skipped")

    @tray_app.command("notwork")
    def tray_notwork(ref: str) -> None:
        """Not work: skip it and never offer this series/title again."""
        t = tracker()
        try:
            t.not_work(_entry(t, ref))
        except TrackerError as exc:
            raise _fail(exc) from exc
        typer.echo("noted: it will not be offered again")

    @tray_app.command("all")
    def tray_all(yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
        """Send everything that has a project (the rest needs `tt tray send <n> -p KEY`)."""
        t = tracker()
        out = t.send_all_unsent()
        typer.echo(f"queued {len(out['sent'])}; {len(out['needs_project'])} need a project")
        _deliver_if_logged_in(t, yes)

    # ------------------------------------------------------------------ recurring
    recur_app = typer.Typer(help="Recurring items: training, courses, reading, sport", no_args_is_help=True)
    app.add_typer(recur_app, name="recur")

    @recur_app.command("add")
    def recur_add(
        text: Annotated[list[str], typer.Argument(help='"every weekday 7:00 run 30m"')],
        send_each: Annotated[
            bool, typer.Option("--send-each", help="development: send every completed instance")
        ] = False,
        preview: Annotated[bool, typer.Option("--preview")] = False,
    ) -> None:
        t = tracker()
        line = " ".join(text)
        try:
            if preview:
                for r in t.preview(line):
                    typer.echo(
                        f"{r['title']}: {r.get('rrule') or 'no recurrence found'}  ({r.get('rrule_text') or ''})"
                    )
                return
            item = t.add_recurring(line)
            if send_each:
                t.set_series_send(item.id, each_instance=True)
        except (TrackerError, ValueError) as exc:
            raise _fail(exc) from exc
        typer.echo(f"{item.title}: {t.describe_rule(item)}  [{item.category.value}]  {short(item.id)}")

    @recur_app.command("list")
    def recur_list() -> None:
        t = tracker()
        for m in t.store.series():
            typer.echo(
                f"{short(m.id)}  {m.title:<28.28} {t.describe_rule(m)}  [{m.category.value}]"
                + ("  send each" if m.send_each_instance else "")
            )

    @recur_app.command("end")
    def recur_end(ref: str, until: Annotated[str, typer.Option("--until", help="YYYY-MM-DD")]) -> None:
        t = tracker()
        try:
            t.end_series(t.find(ref), date.fromisoformat(until))
        except (TrackerError, ValueError) as exc:
            raise _fail(exc) from exc
        typer.echo("ended")

    # ------------------------------------------------------------------ items
    item_app = typer.Typer(
        help="Complete, skip, reopen, delete or recategorise an item", no_args_is_help=True
    )
    app.add_typer(item_app, name="item")

    for verb, fn in (("done", "complete"), ("skip", "skip"), ("reopen", "reopen")):

        def _make(fn_name: str = fn, verb_name: str = verb) -> None:
            @item_app.command(verb_name)
            def _cmd(ref: str) -> None:
                t = tracker()
                try:
                    res = getattr(t, fn_name)(t.find(ref))
                except TrackerError as exc:
                    raise _fail(exc) from exc
                typer.echo(f"{res.title}: {res.status.value}")

        _make()

    @item_app.command("delete")
    def item_delete(ref: str) -> None:
        t = tracker()
        try:
            ok = t.delete(t.find(ref))
        except TrackerError as exc:
            raise _fail(exc) from exc
        typer.echo("deleted" if ok else "not found")

    @item_app.command("category")
    def item_category(
        ref: str, category: Annotated[str, typer.Argument(help="work | meeting | development | personal")]
    ) -> None:
        t = tracker()
        try:
            it = t.set_category(t.find(ref), Category(category))
        except (TrackerError, ValueError) as exc:
            raise _fail(exc) from exc
        typer.echo(f"{it.title}: {it.category.value} (share: {it.share_policy.value})")

    @item_app.command("share")
    def item_share(ref: str, policy: Annotated[str, typer.Argument(help="never | ask | auto")]) -> None:
        t = tracker()
        try:
            it = t.set_share(t.find(ref), SharePolicy(policy))
        except (TrackerError, ValueError) as exc:
            raise _fail(exc) from exc
        typer.echo(f"{it.title}: share {it.share_policy.value}")

    # ------------------------------------------------------------------ reviews
    @app.command("eod")
    def eod_cmd(
        send_all: Annotated[
            bool, typer.Option("--send-all", help="send every unsent work item that has a project")
        ] = False,
        confirm: Annotated[
            bool, typer.Option("--confirm", help="confirm the server DailyLog (only if something was sent)")
        ] = False,
        as_json: AsJson = False,
    ) -> None:
        """End of day: hours by category, what is still unsent, [Send all], confirm the DailyLog."""
        t = tracker()
        card = t.end_of_day()
        if as_json:
            typer.echo(json.dumps(card, indent=2))
        else:
            h = card["hours_by_category"]
            typer.echo(
                f"{card['date']}: "
                + "  ".join(f"{c} {v} h" for c, v in h.items())
                + f"   done {card['done_count']}, sent {card['sent_count']}"
            )
            for u in card["unsent"]:
                typer.echo(f"  unsent: {u['label']}")
        if send_all:
            res = t.send_all_unsent()
            typer.echo(f"queued {len(res['sent'])}; {len(res['needs_project'])} need a project")
            _deliver_if_logged_in(t, True)
        if confirm:
            cfg: ClientConfig = t.env.extra["cfg"]
            if not (cfg.workspace and cfg.handle):
                typer.echo("not logged in: nothing to confirm", err=True)
                raise typer.Exit(2)
            from tt_client.api import TTApi, Unreachable

            try:
                typer.echo(t.confirm_daily_log(TTApi(cfg))["sentence"])
            except Unreachable:
                typer.echo("offline: the daily log will be confirmed when you are back", err=True)
                raise typer.Exit(1) from None

    @app.command("review")
    def review_cmd(
        share_development: Annotated[
            bool, typer.Option("--share-development", help="queue a development summary note")
        ] = False,
        as_json: AsJson = False,
    ) -> None:
        """Weekly review: planned vs done, development hours, meeting load, habit streaks."""
        t = tracker()
        card = t.weekly_review()
        if as_json:
            typer.echo(json.dumps(card, indent=2))
        else:
            typer.echo(
                f"week of {card['week_start']}: planned {card['planned_h']} h, done {card['done_h']} h ({card['completion_pct']} %)"
            )
            typer.echo(
                f"development {card['development_h']} h · meetings {card['meetings_h']} h ({card['meeting_load_pct']} % of capacity)"
            )
            for s in card["streaks"]:
                typer.echo(f"  streak {s['streak']:>2}  {s['title']}  ({s['done']}/{s['of']})")
        if share_development:
            try:
                typer.echo("queued: " + t.development_summary()["note"])
            except TrackerError as exc:
                raise _fail(exc) from exc

    # ------------------------------------------------------------------ privacy
    @app.command("privacy")
    def privacy_cmd(
        first_run: Annotated[
            bool, typer.Option("--first-run", help="record the three category choices")
        ] = False,
        work: Annotated[str, typer.Option(help="ask | auto | never")] = "ask",
        meeting: Annotated[str, typer.Option(help="ask | auto | never")] = "ask",
        development: Annotated[
            bool,
            typer.Option("--development/--no-development", help="send development items as #development"),
        ] = False,
        assist: Annotated[
            bool | None,
            typer.Option(
                "--assist/--no-assist",
                help="let the [llm] gateway classify quick-add lines the parser cannot place (default: off)",
            ),
        ] = None,
        as_json: AsJson = False,
    ) -> None:
        """What stays on this device, what can be sent, and the three category toggles."""
        t = tracker()
        try:
            if first_run:
                p = t.first_run(work=work, meeting=meeting, development=development, assist=bool(assist))
            elif assist is not None:
                p = t.set_assist(assist)
            else:
                p = t.privacy()
        except ValueError as exc:
            raise _fail(exc) from exc
        if as_json:
            typer.echo(json.dumps(p, indent=2))
            return
        typer.echo(p["stays_on_device"])
        typer.echo(f"store: {p['store']}  (encrypted; key in {p['key_store']})")
        c = p["categories"]
        typer.echo(
            f"work: {c['work']} · meetings: {c['meeting']} · development: {c['development']} · personal: never (no way to send it)"
        )
        typer.echo(f"assist: {p['assist']}  ({p['assist_note']})")
        typer.echo("calendars: " + (", ".join(p["calendars"]) or "none"))
        if not p["first_run_done"]:
            typer.echo(
                "\nchoose once: `tt privacy --first-run --work ask --meeting ask --no-development --no-assist`"
            )

    @app.command("sent")
    def sent_cmd(
        action: Annotated[str | None, typer.Argument(help="unsend")] = None,
        n: Annotated[int | None, typer.Argument()] = None,
        as_json: AsJson = False,
    ) -> None:
        """What has been sent (with server keys); `tt sent unsend <n>` takes one back within 10 minutes."""
        t = tracker()
        rows = t.sent_audit()
        if action == "unsend":
            if not n or not 1 <= n <= len(rows):
                raise typer.BadParameter("give the number from `tt sent`")
            try:
                how = t.unsend(rows[n - 1]["item"])
            except (TrackerError, NotSendable) as exc:
                raise _fail(exc) from exc
            typer.echo(
                "taken back before it reached the server"
                if how == "discarded"
                else "correction queued (time.unlog)"
            )
            return
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
            return
        if not rows:
            typer.echo("nothing sent yet")
        for i, r in enumerate(rows, 1):
            state = "unsent" if r["unsent"] else "can unsend" if r["can_unsend"] else "sent"
            typer.echo(
                f"{i}. {r['at'][:16]}  {r['title']:<28.28} {_h(r['minutes']):>8} → {r['project']} {r['server_key'] or '(queued)'}  [{state}]"
            )

    @app.command("erase-local")
    def erase_cmd(yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
        """Erase ALL local personal data: the store, its key and the calendar tokens. The server copy of what you sent stays."""
        if not yes and not (
            sys.stdin.isatty()
            and typer.confirm("Erase the personal store, its key and calendar tokens?", default=False)
        ):
            typer.echo("nothing erased (pass --yes)")
            raise typer.Exit(1)
        out = tracker().erase_local()
        typer.echo(
            f"erased {', '.join(out['files']) or 'no files'}; removed secrets: {', '.join(out['secrets']) or 'none'}"
        )

    @app.command("export")
    def export_cmd(path: Path) -> None:
        """Backup the whole personal store as JSON (plaintext, mode 0600)."""
        typer.echo(f"wrote {tracker().export_json(path)}")

    @app.command("import")
    def import_cmd(path: Path, replace: Annotated[bool, typer.Option("--replace")] = False) -> None:
        """Restore a backup (newest edit wins unless --replace)."""
        try:
            counts = tracker().import_json(path, replace=replace)
        except Exception as exc:
            raise _fail(exc) from exc
        typer.echo(f"imported {counts}")

    @app.command("sidecar", hidden=True)
    def sidecar_cmd(command: str, payload: Annotated[str, typer.Argument()] = "{}") -> None:
        """The desktop shells call this: `tt sidecar <command> '<json>'` prints one JSON document (ADR-0038)."""
        from tt_client.personal.bridge import handle

        typer.echo(json.dumps(handle(tracker(), command, json.loads(payload or "{}"))))


def _deliver_if_logged_in(t: Tracker, yes: bool) -> None:
    cfg: ClientConfig = t.env.extra["cfg"]
    if not (cfg.workspace and cfg.handle and cfg.token):
        typer.echo("queued in the outbox; log in with `tt login` to deliver")
        return
    from tt_client.api import TTApi

    out = t.deliver(TTApi(cfg))
    typer.echo(
        "offline: queued, `tt sync` delivers later"
        if out["offline"]
        else f"delivered {out['sent']} batch(es)"
    )
