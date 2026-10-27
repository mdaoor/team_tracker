"""`Tracker`: the personal tracker as one object (Phase 15). The CLI, the desktop sidecar and the tests all drive this and nothing else.

It owns the encrypted store, the timer, the rules and the send path. It opens no sockets: `deliver()` is the only method that
takes an API object (the existing `TTApi`), and the calendar adapters live in `tt_client.integrations`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from tt_schema.base import new_id
from tt_schema.client import ClientAction

from tt_client.outbox import Outbox, sync
from tt_client.parser.personal import parse_personal
from tt_client.parser.structured import ParseContext
from tt_client.personal import rules as rules_mod
from tt_client.personal import send as send_mod
from tt_client.personal import tray as tray_mod
from tt_client.personal import views
from tt_client.personal.model import (
    CATEGORY_DEFAULT_SHARE,
    Category,
    Draft,
    PersonalItem,
    SharePolicy,
    Status,
)
from tt_client.personal.recurrence import describe, materialise, split_instance_id, validate_rrule, zone
from tt_client.personal.settings import Settings
from tt_client.personal.store import PersonalStore, db_files
from tt_client.personal.timer import TimerEngine
from tt_client.rows import DayDraft
from tt_client.secrets import SecretStore

DB_NAME = "personal.db"


class TrackerError(Exception):
    pass


@dataclass
class Env:
    data_dir: Path
    secrets: SecretStore
    tz: str = "UTC"
    workspace: str = ""
    actor: str = ""  # "@handle"
    email: str | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    pack: Callable[[], dict[str, Any] | None] = lambda: None
    extra: dict[str, Any] = field(default_factory=dict)


class Tracker:
    def __init__(self, env: Env, store: PersonalStore | None = None) -> None:
        self.env = env
        self.store = store or PersonalStore.open(env.data_dir / DB_NAME, env.secrets)
        self.settings = Settings(self.store)
        self.timers = TimerEngine(self.store, env.clock)
        self.outbox = Outbox(env.data_dir)

    # ------------------------------------------------------------------ plumbing
    @property
    def now(self) -> datetime:
        return self.env.clock()

    @property
    def today(self) -> date:
        return self.now.astimezone(zone(self.env.tz)).date()

    def hints(self) -> rules_mod.Hints:
        return rules_mod.Hints.from_pack(self.env.pack(), my_email=self.env.email)

    def _pack_capacity(self) -> float | None:
        return self.hints().capacity_hours_per_week

    def _server_items(self) -> list[dict[str, Any]]:
        pack = self.env.pack() or {}
        return [{**i, "server": True} for i in pack.get("my_open_items", [])]

    def parse_ctx(self) -> ParseContext:
        return ParseContext(actor=self.env.actor or "@me", now=self.now, tz=self.env.tz)

    # ------------------------------------------------------------------ quick add and recurring editor
    def preview(self, line: str) -> list[dict[str, Any]]:
        """What the line would create, without creating it (the recurring editor shows `rrule_text`)."""
        return [d.model_dump(mode="json") for d in parse_personal(line, self.parse_ctx())]

    def add(
        self, line: str, assist: Callable[[str], list[dict[str, Any]] | None] | None = None
    ) -> list[PersonalItem]:
        """Parse one line into items. `assist(line)` may return the gateway's classification; it only refines undecided kinds.

        The line leaves the device only when the person turned `privacy.assist` on AND the deterministic parser left a
        kind undecided (`kind_source == "default"`); otherwise `assist` is never called."""
        drafts = parse_personal(line, self.parse_ctx())
        if (
            drafts
            and assist is not None
            and self.settings.get("privacy.assist") == "on"
            and any(d.kind_source == "default" for d in drafts)
        ):
            from tt_client.parser.personal_llm import apply_assist

            drafts = apply_assist(drafts, assist(line))
        if not drafts:
            raise TrackerError("nothing to add: try `run 30m at 6pm` or `every weekday 7:00 run 30m`")
        return [self.add_draft(d) for d in drafts]

    def add_draft(self, draft: Draft) -> PersonalItem:
        item = draft.to_item(self.env.tz)
        if item.rrule:
            validate_rrule(item.rrule, self.env.tz)
        sug = rules_mod.suggest(item, self.hints(), self.store.rules())
        item.category, item.share_policy = (
            sug.category,
            (item.share_policy if item.category == sug.category else CATEGORY_DEFAULT_SHARE[sug.category]),
        )
        if sug.project and not item.project_hint and sug.source in ("rule", "series"):
            item.project_hint = sug.project
        return self.store.put_item(item)

    def add_recurring(self, text: str) -> PersonalItem:
        """`tt recur add "every weekday 7:00 run 30m"`: like add, but the line must contain a recurrence."""
        drafts = [d for d in parse_personal(text, self.parse_ctx()) if d.rrule]
        if not drafts:
            raise TrackerError(
                "no recurrence found: try `every weekday 7:00 run 30m` or `Tue/Thu 18:00 Arabic course 1h`"
            )
        return self.add_draft(drafts[0])

    def set_series_send(self, series_id: str, *, each_instance: bool) -> PersonalItem:
        master = self._require(series_id)
        master.send_each_instance = each_instance and master.category != Category.PERSONAL
        return self.store.put_item(master)

    def end_series(self, series_id: str, until: date) -> PersonalItem:
        master = self._require(series_id)
        if not master.rrule:
            raise TrackerError("not a recurring item")
        parts = [p for p in master.rrule.split(";") if not p.startswith(("UNTIL=", "COUNT="))]
        master.rrule = ";".join([*parts, f"UNTIL={until.strftime('%Y%m%d')}T235959"])
        return self.store.put_item(master)

    # ------------------------------------------------------------------ items
    def _require(self, item_id: str) -> PersonalItem:
        item = self.store.get_item(item_id)
        if item is None and (parts := split_instance_id(item_id)):
            item = self._realise(*parts)
        if item is None:
            raise TrackerError(f"no such item: {item_id}")
        return item

    def _realise(self, master_id: str, day: date) -> PersonalItem | None:
        """Turn a generated instance into a stored override row so it can be completed, timed, moved or sent."""
        master = self.store.get_item(master_id)
        if master is None or not master.is_recurring:
            return None
        row = next((o for o in self.store.overrides_for(master_id) if o.instance_date == day), None)
        if row is not None:
            return row
        start, end = views.day_bounds(day, master.tz)
        inst = next((i for i in materialise(master, start, end) if i.instance_date == day), None)
        if inst is None:
            return None
        return self.store.put_item(inst)

    def get(self, item_id: str) -> PersonalItem:
        return self._require(item_id)

    def find(self, ref: str) -> str:
        """Resolve a full id or a unique prefix (what `tt day` prints) among the items around today to an id."""
        if self.store.get_item(ref) is not None or split_instance_id(ref):
            return ref
        if "@" in ref:  # `0192ab34@2026-10-27`: a series prefix and the occurrence date
            head, _, day = ref.rpartition("@")
            masters = [m.id for m in self.store.series() if m.id.endswith(head) or m.id.startswith(head)]
            if len(masters) == 1 and split_instance_id(f"{masters[0]}@{day}"):
                return f"{masters[0]}@{day}"
            raise TrackerError(f"{len(masters)} recurring items match {head!r}")
        start, _ = views.day_bounds(self.today - timedelta(days=30), self.env.tz)
        _, end = views.day_bounds(self.today + timedelta(days=60), self.env.tz)
        ids = {i.id for i in views.items_between(self.store, start, end)} | {i.id for i in self.store.items()}
        hits = sorted(
            i for i in ids if i.endswith(ref) or i.startswith(ref)
        )  # the tail is the random part of a time-ordered id
        if len(hits) != 1:
            raise TrackerError(
                f"{len(hits)} items match {ref!r}"
                + (f": {', '.join(h[:14] for h in hits[:5])}" if hits else "")
            )
        return hits[0]

    def calendar(self, providers: dict[str, Any]) -> Any:
        """A `CalendarSync` over this store for the given providers (built lazily: the tracker itself never imports httpx)."""
        from tt_client.integrations.sync import CalendarSync

        me = {name: self.env.email for name in providers if self.env.email}
        return CalendarSync(self.store, providers, tz=self.env.tz, me=me, clock=self.env.clock)

    def complete(self, item_id: str, minutes: int | None = None) -> PersonalItem:
        item = self._require(item_id)
        running = self.store.get_timer(item.id)
        if running is not None and running.state != "stopped":
            self.timers.stop(item.id)
            item = self._require(item.id)
        item.status = Status.DONE
        if minutes is not None:
            item.actual_min = minutes
        return self.store.put_item(item)

    def skip(self, item_id: str) -> PersonalItem:
        item = self._require(item_id)
        item.status = Status.SKIPPED
        return self.store.put_item(item)

    def reopen(self, item_id: str) -> PersonalItem:
        item = self._require(item_id)
        item.status = Status.PLANNED
        return self.store.put_item(item)

    def delete(self, item_id: str) -> bool:
        parts = split_instance_id(item_id)
        if parts and self.store.get_item(item_id) is None:
            item = self._realise(*parts)
            if item is None:
                return False
            item.status = Status.CANCELLED  # an override that drops the occurrence
            self.store.put_item(item)
            return True
        return self.store.delete_item(item_id)

    def reschedule(self, item_id: str, start: datetime) -> PersonalItem:
        item = self._require(item_id)
        dur = item.duration_min
        item.start, item.all_day = start, False
        item.end = start + timedelta(minutes=dur) if dur else None
        return self.store.put_item(item)

    def set_category(self, item_id: str, category: Category) -> PersonalItem:
        item = self._require(item_id)
        item.category = category
        item.share_policy = CATEGORY_DEFAULT_SHARE[category]
        if category == Category.DEVELOPMENT and "development" not in item.tags:
            item.tags.append("development")
        return self.store.put_item(item)

    def set_share(self, item_id: str, policy: SharePolicy) -> PersonalItem:
        item = self._require(item_id)
        item.share_policy = (
            SharePolicy.NEVER if item.category == Category.PERSONAL else policy
        )  # personal: no such control
        return self.store.put_item(item)

    # ------------------------------------------------------------------ timer
    def timer_start(self, item_id: str | None = None, *, title: str | None = None) -> dict[str, Any]:
        if item_id is None:
            if not title:
                raise TrackerError("give an item id or a title")
            item = self.add_draft(Draft(title=title, all_day=True, start=self._today_start()))
        else:
            item = self._require(item_id)
        timer, paused = self.timers.start(item.id)
        out: dict[str, Any] = {"item": item.id, "title": item.title, "state": timer.state}
        if paused:
            out["paused"] = paused.item_id
        self._mirror(item, ClientAction.START)
        return out

    def timer_stop(self) -> dict[str, Any] | None:
        res = self.timers.stop()
        if res is None:
            return None
        timer, minutes = res
        item = self.store.get_item(timer.item_id)
        return {"item": timer.item_id, "title": item.title if item else None, "minutes": minutes}

    def timer_status(self) -> dict[str, Any] | None:
        t = self.timers.current()
        if t is None:
            return None
        item = self.store.get_item(t.item_id)
        return {
            "item": t.item_id,
            "title": item.title if item else None,
            "elapsed_min": t.elapsed_min(self.now),
            "state": t.state,
        }

    def _mirror(self, item: PersonalItem, action: ClientAction) -> None:
        batch = send_mod.mirror_timer(
            item, action, actor=self.env.actor, workspace=self.env.workspace, now=self.now
        )
        if batch is not None:
            self.outbox.write(batch.model_dump(mode="json", exclude_none=True))

    def _today_start(self) -> datetime:
        return views.day_bounds(self.today, self.env.tz)[0]

    # ------------------------------------------------------------------ views
    def day(self, day: date | None = None) -> dict[str, Any]:
        return views.day_view(
            self.store, day or self.today, self.env.tz, self.now, self.settings,
            server_items=self._server_items(), pack_capacity=self._pack_capacity(),
        )  # fmt: skip

    def week(self, day: date | None = None) -> dict[str, Any]:
        return views.week_view(
            self.store,
            day or self.today,
            self.env.tz,
            self.now,
            self.settings,
            pack_capacity=self._pack_capacity(),
        )

    def month(self, year: int | None = None, month: int | None = None) -> dict[str, Any]:
        t = self.today
        return views.month_view(
            self.store, year or t.year, month or t.month, self.env.tz, self.now, self.settings
        )

    # ------------------------------------------------------------------ tray and send
    def tray(self, *, include_auto: bool = True) -> list[tray_mod.TrayEntry]:
        return tray_mod.ready_to_send(
            self.store, self.settings, self.hints(), self.now, self.env.tz, include_auto=include_auto
        )

    def tray_rows(self) -> list[dict[str, Any]]:
        z = zone(self.env.tz)
        return [e.as_dict(z) for e in self.tray()]

    def send(
        self,
        item_id: str,
        *,
        project: str | None = None,
        note: str | None = None,
        minutes: int | None = None,
        learn: bool = True,
        auto: bool = False,
    ) -> dict[str, Any]:
        """`auto=True` is a standing rule sending without a tap: neutral title and note until the series' note template was confirmed."""
        item = self._require(item_id)
        if item.category == Category.PERSONAL:  # checked first: no send path exists, whatever else is missing
            raise send_mod.NotSendable("personal items are never sent")
        hints = self.hints()
        sug = rules_mod.suggest(item, hints, self.store.rules())
        chosen = project or item.project_hint or sug.project
        if item.category != Category.DEVELOPMENT and not chosen:
            raise send_mod.NeedsProject(
                f"choose a project ({', '.join(sug.alternatives or hints.project_keys[:3]) or 'none known'})"
            )
        item, msg = send_mod.send_item(
            item, outbox=self.outbox, workspace=self.env.workspace, actor=self.env.actor, project=chosen,
            dev_project=hints.development_project, now=self.now, minutes=minutes, note=note,
            neutral=auto and send_mod.neutralised_when_auto(item) and send_mod.note_consent_at(self.store, item) is None,
        )  # fmt: skip
        self.store.put_item(item)
        if (
            not auto
            and send_mod.neutralised_when_auto(item)
            and (note is None or note == send_mod.note_template(item))
        ):
            send_mod.confirm_note_template(
                self.store, item, self.now
            )  # the person saw this exact note and sent it
        self._remember_on_series(item, chosen)
        if learn and chosen and item.category in (Category.MEETING, Category.WORK):
            rules_mod.learn_mapping(self.store, item, chosen)
        return {
            "item": item.id,
            "client_id": msg.client_id,
            "project": msg.project,
            "minutes": msg.minutes,
            "tags": msg.tags,
        }

    def _remember_on_series(self, item: PersonalItem, project: str | None) -> None:
        if item.series_id and (master := self.store.get_item(item.series_id)) is not None and project:
            if master.category != Category.DEVELOPMENT:
                master.project_hint = project
                self.store.put_item(master)

    def send_batch(self, item_ids: list[str]) -> list[dict[str, Any]]:
        return [self.send(i) for i in item_ids]

    def autosend(self) -> list[dict[str, Any]]:
        """Entries a standing rule says to send without a tap (`share.*` = auto, or `send each instance`)."""
        sent: list[dict[str, Any]] = []
        for e in self.tray():
            if e.auto:
                try:
                    sent.append(self.send(e.item.id, project=e.suggestion.project, auto=True))
                except send_mod.NotSendable:
                    continue
        return sent

    def not_work(self, item_id: str) -> PersonalItem:
        """The tray's [Not work]: skip this occurrence and remember it for the series/title."""
        item = self._require(item_id)
        rules_mod.learn_not_work(self.store, item)
        item.status = Status.SKIPPED
        return self.store.put_item(item)

    def remember_share(self, category: Category, mode: str) -> None:
        """ "Ask once then remember": `tt` stores the answer (e.g. `work` -> `auto`)."""
        if category == Category.DEVELOPMENT:
            self.settings.set("share.development", mode in ("ask", "auto"))
        else:
            self.settings.set(f"share.{category.value}", mode)

    def unsend(self, item_id: str) -> str:
        item = self._require(item_id)
        how = send_mod.unsend(
            item, outbox=self.outbox, workspace=self.env.workspace, actor=self.env.actor, now=self.now,
            window_min=int(self.settings.get("unsend_minutes")),
        )  # fmt: skip
        self.store.put_item(item)
        return how

    def deliver(self, api: Any) -> dict[str, Any]:
        """Push the outbox with the ordinary sync, then record the server's keys on the sent items."""
        report = sync(api, self.outbox)
        changed = send_mod.apply_results([i for i in self.store.items() if i.sent_refs], report.results or [])
        for i in changed:
            self.store.put_item(i)
        return {
            "sent": report.sent,
            "offline": report.offline,
            "kept": report.kept,
            "failed": report.failed_messages,
        }

    # ------------------------------------------------------------------ reviews
    def _review_inputs(self) -> views.ReviewInputs:
        return views.ReviewInputs(self.store, self.settings, self.env.tz, self.now, self._pack_capacity())

    def end_of_day(self, day: date | None = None) -> dict[str, Any]:
        day = day or self.today
        start, end = views.day_bounds(day, self.env.tz)
        z = zone(self.env.tz)
        unsent = [e.as_dict(z) for e in self.tray() if start <= (e.item.start or start) < end]
        return views.end_of_day_card(self._review_inputs(), day, unsent)

    def send_all_unsent(self, day: date | None = None) -> dict[str, Any]:
        """The end-of-day [Send all]: every unsent work item of the day that has a project (or is development)."""
        day = day or self.today
        start, end = views.day_bounds(day, self.env.tz)
        sent, needs_project = [], []
        for e in self.tray():
            if not (start <= (e.item.start or start) < end):
                continue
            if e.suggestion.project is None and e.item.category != Category.DEVELOPMENT:
                needs_project.append(e.item.id)
                continue
            sent.append(self.send(e.item.id, project=e.suggestion.project))
        return {"sent": sent, "needs_project": needs_project}

    def confirm_daily_log(self, api: Any, day: date | None = None) -> dict[str, Any]:
        """Confirm the server DailyLog for `day`.

        The planner's own "sent" list gates it only when the server has no time for the day: work reported through
        `tt say` makes a draft with minutes by itself, and that is as confirmable as anything sent from the planner."""
        day = day or self.today
        start, end = views.day_bounds(day, self.env.tz)
        sent_today = any(
            r.unsent_at is None and start <= r.at < end + timedelta(days=1)
            for i in self.store.items() for r in i.sent_refs
        )  # fmt: skip
        if not sent_today:
            existing = api.daily_logs(day.isoformat())
            if not existing or not (existing[0].get("minutes_total") or 0):
                return {
                    "confirmed": False,
                    "reason": "nothing was sent today",
                    "sentence": "Nothing to confirm yet: nothing was sent today.",
                }
        self.deliver(api)
        logs = api.daily_logs(day.isoformat())
        if not logs:
            return {
                "confirmed": False,
                "reason": "no daily log yet",
                "sentence": "Your day has no draft on the server yet: run `tt sync` and try again.",
            }
        draft = DayDraft.from_api(logs[0])
        if draft.status == "confirmed":
            return {
                "confirmed": True,
                "minutes_total": draft.minutes_total,
                "sentence": draft.already_sentence(),
            }
        api.confirm_daily_log(logs[0]["id"])
        return {
            "confirmed": True,
            "minutes_total": logs[0].get("minutes_total"),
            "sentence": draft.confirmed_sentence(),
        }

    def weekly_review(self, day: date | None = None) -> dict[str, Any]:
        return views.weekly_review_card(self._review_inputs(), day or self.today)

    def development_summary(self, day: date | None = None) -> dict[str, Any]:
        """Optional "share development summary": queues one `note` on the development project. Only on explicit request."""
        from tt_schema.client import (
            CLIENT_SCHEMA_MINOR,
            ClientBatch,
            ClientMessage,
            ClientSource,
            ClientTaskRef,
        )

        card = self.weekly_review(day)
        if not card["development_items"]:
            raise TrackerError("no development time this week")
        lines = ", ".join(f"{i['title']} ({i['hours']} h)" for i in card["development_items"])
        text = f"Week of {card['week_start']}: {card['development_h']} h development: {lines}"
        msg = ClientMessage(
            actor=self.env.actor, action=ClientAction.NOTE, at=self.now, client_id=new_id(), note=text,
            project=self.hints().development_project, tags=["development"], item=ClientTaskRef(title="Development summary"),
            source=ClientSource(origin="personal"),
        )  # fmt: skip
        batch = ClientBatch(workspace=self.env.workspace, messages=[msg], client_version=CLIENT_SCHEMA_MINOR)
        self.outbox.write(batch.model_dump(mode="json", exclude_none=True))
        return {"client_id": msg.client_id, "note": text}

    # ------------------------------------------------------------------ privacy
    def privacy(self) -> dict[str, Any]:
        s = self.settings
        return {
            "stays_on_device": "Your personal items stay on this device. Meetings and work you choose to send go to Team Tracker.",
            "store": str(self.env.data_dir / DB_NAME),
            "encrypted": True,
            "key_store": self.env.secrets.kind,
            "categories": {
                "work": s.share_mode(Category.WORK),
                "meeting": s.share_mode(Category.MEETING),
                "development": "on" if s.get("share.development") else "off",
                "personal": "never",
            },
            "assist": str(s.get("privacy.assist")),
            "assist_note": "off: quick-add lines never leave this device. on: a line the parser cannot place may be sent to your "
            "[llm] gateway (never to the Team Tracker server).",
            "first_run_done": bool(s.get("privacy.first_run_done")),
            "calendars": [n.split(":", 1)[1] for n in self.env.secrets.names() if n.startswith("oauth:")],
        }

    def first_run(
        self, *, work: str = "ask", meeting: str = "ask", development: bool = False, assist: bool = False
    ) -> dict[str, Any]:
        self.settings.set("privacy.assist", "on" if assist else "off")
        self.settings.set("share.work", work)
        self.settings.set("share.meeting", meeting)
        self.settings.set("share.development", development)
        self.settings.set("privacy.first_run_done", True)
        return self.privacy()

    def set_assist(self, on: bool) -> dict[str, Any]:
        self.settings.set("privacy.assist", "on" if on else "off")
        return self.privacy()

    def sent_audit(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        window = int(self.settings.get("unsend_minutes"))
        for item in self.store.items(include_deleted=True):
            for ref in item.sent_refs:
                rows.append(
                    {
                        "item": item.id,
                        "title": item.title,
                        "client_id": ref.client_id,
                        "server_key": ref.server_key,
                        "project": ref.project,
                        "minutes": ref.minutes,
                        "at": ref.at.isoformat(),
                        "unsent": ref.unsent_at is not None,
                        "can_unsend": send_mod.can_unsend(ref, self.now, window),
                    }
                )
        return sorted(rows, key=lambda r: r["at"], reverse=True)

    def export_json(self, path: Path) -> Path:
        path.write_text(json.dumps(self.store.export_doc(), indent=2))
        path.chmod(0o600)
        return path

    def import_json(self, path: Path, *, replace: bool = False) -> dict[str, int]:
        return self.store.import_doc(json.loads(path.read_text()), replace=replace)

    def erase_local(self) -> dict[str, Any]:
        """ "Erase all local data": the store (and its WAL files), the encryption key, calendar tokens and sync state."""
        path = self.env.data_dir / DB_NAME
        self.store.wipe()
        self.store.close()
        removed_files = []
        for f in db_files(path):
            if f.exists():
                f.unlink()
                removed_files.append(f.name)
        removed_secrets = []
        for name in self.env.secrets.names():
            if name.startswith(("oauth:", "personal-store-key")):
                self.env.secrets.delete(name)
                removed_secrets.append(name)
        return {"files": removed_files, "secrets": removed_secrets}

    # ------------------------------------------------------------------ helpers for calendar sync
    def describe_rule(self, item: PersonalItem) -> str | None:
        return (
            describe(
                item.rrule, item.start.astimezone(zone(item.tz)) if item.start and not item.all_day else None
            )
            if item.rrule
            else None
        )
