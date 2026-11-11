"""The "ready to send" tray (Phase 15 §15.2/§15.3): finished meetings and done work, each with a suggestion and one tap to send.

A meeting appears at `end + 2 min` when the app is running, or at the next launch (the tray is computed from the store, so it is simply
"everything that ended and was not handled"). Category defaults (plan principle 4):

* `meeting`      offered after every meeting (setting `share.meeting`: ask | auto | never);
* `work`         offered when done; `ask` once, the answer is remembered (`share.work`);
* `development`  not offered unless the one toggle `share.development` is on; then sent tagged `#development` to the DEV project;
* `personal`     never offered: there is no send path (ADR-0053).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tt_client.personal.model import Category, Kind, PersonalItem, SharePolicy, Status
from tt_client.personal.rules import Hints, Suggestion, suggest
from tt_client.personal.send import auto_note, note_template
from tt_client.personal.settings import Settings
from tt_client.personal.store import PersonalStore
from tt_client.personal.views import items_between

DONE_STATES = (Status.DONE,)


@dataclass
class TrayEntry:
    item: PersonalItem
    suggestion: Suggestion
    minutes: int
    note: str
    due_at: datetime
    auto: bool  # a standing rule says send without a tap
    auto_note: str = ""  # the exact note such an automatic send carries now (neutral until the series' template is confirmed)

    def as_dict(self, tz: Any = UTC) -> dict[str, Any]:
        it = self.item
        return {
            "id": it.id,
            "title": it.title,
            "kind": it.kind.value,
            "category": it.category.value,
            "start": it.start.astimezone(tz).isoformat() if it.start else None,
            "end": it.effective_end.astimezone(tz).isoformat() if it.effective_end else None,
            "minutes": self.minutes,
            "note": self.note,
            "due_at": self.due_at.isoformat(),
            "auto": self.auto,
            "auto_sends": self.auto_note,
            "online_meeting_url": it.online_meeting_url,
            "suggestion": self.suggestion.as_dict(),
            "needs_project": self.suggestion.project is None and it.category != Category.DEVELOPMENT,
            "label": f"Send {_h(self.minutes)} {it.title}"
            + (f" → {self.suggestion.project}" if self.suggestion.project else ""),
        }


def _h(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h" if h and not m else f"{h} h {m} min" if h else f"{m} min"


def _suppressed(item: PersonalItem, settings: Settings) -> bool:
    emails = {a.lower() for a in item.attendees}
    if emails & {a.lower() for a in settings.get("suppress.attendees")}:
        return True
    domains = {d.lower() for d in settings.get("suppress.domains")}
    return bool(domains & {e.rsplit("@", 1)[1] for e in emails if "@" in e})


def ready_to_send(
    store: PersonalStore,
    settings: Settings,
    hints: Hints,
    now: datetime,
    tz: str,
    *,
    include_auto: bool = True,
) -> list[TrayEntry]:
    delay = timedelta(minutes=int(settings.get("tray.delay_min")))
    horizon = now - timedelta(days=int(settings.get("tray.max_age_days")))
    rules = store.rules()
    out: list[TrayEntry] = []
    for item in items_between(store, horizon, now + timedelta(days=1)):
        if item.is_sent or item.status in (Status.SKIPPED, Status.CANCELLED) or item.deleted_at is not None:
            continue
        sug = suggest(item, hints, rules)
        category = sug.category
        if category == Category.PERSONAL or sug.blocked:
            continue
        if category != Category.DEVELOPMENT and item.share_policy == SharePolicy.NEVER:
            continue
        mode = settings.share_mode(category)
        if item.share_policy == SharePolicy.AUTO or (
            category == Category.DEVELOPMENT and item.send_each_instance
        ):
            mode = "auto"  # an explicit standing choice for this item or series
        if mode == "never":
            continue
        end = item.effective_end
        ended = end is not None and end + delay <= now
        if item.kind == Kind.MEETING and category in (Category.MEETING, Category.WORK):
            if not ended or item.status not in (Status.PLANNED, Status.DONE):
                continue  # a meeting is offered once it has ended, without any tap of "done"
        elif item.status not in DONE_STATES:
            continue  # everything else is offered when the person marks it done
        if _suppressed(item, settings):
            continue
        minutes = item.minutes or 0
        if minutes <= 0:
            continue
        out.append(
            TrayEntry(
                item=item,
                suggestion=sug,
                minutes=minutes,
                note=note_template(item),
                due_at=(end or now) + delay,
                auto=mode == "auto" and (sug.project is not None or category == Category.DEVELOPMENT),
                auto_note=auto_note(store, item, minutes),
            )
        )
    out.sort(key=lambda e: e.due_at, reverse=True)
    return [e for e in out if include_auto or not e.auto]


def due_notifications(store: PersonalStore, entries: list[TrayEntry]) -> list[TrayEntry]:
    """Entries the shell has not yet notified about ("meeting ended: send?"). Marks them so each notifies once."""
    seen = set(store.get_setting("tray.notified", []))
    fresh = [e for e in entries if e.item.id not in seen]
    if fresh:
        store.set_setting("tray.notified", sorted(seen | {e.item.id for e in fresh})[-500:])
    return fresh
