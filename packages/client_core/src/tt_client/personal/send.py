"""The send path (Phase 15 §15.3, §15.6): a personal item becomes a `ClientBatch` in the ordinary outbox.

Same rules as every other capture: a file in the outbox is the source of truth, the server deduplicates by `client_id` and, for calendar
occurrences, by `calendar_ref` (ADR-0054). The `personal` category has **no** path here: `build_message` raises `NotSendable`.
`calendar_ref` carries ids only; titles, attendees and locations stay on the device unless the person puts them in the note they send.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tt_schema.base import new_id
from tt_schema.client import (
    CLIENT_SCHEMA_MINOR,
    CalendarRef,
    ClientAction,
    ClientBatch,
    ClientMessage,
    ClientSource,
    ClientTaskRef,
)

from tt_client.outbox import Outbox
from tt_client.personal.model import Category, PersonalItem, SentRef
from tt_client.personal.recurrence import split_instance_id


class NotSendable(Exception):
    """The item may not be sent (personal category, nothing to report, already sent)."""


class NeedsProject(NotSendable):
    """A work item or meeting without a project: the UI shows the 3-choice chip row."""


def origin_of(item: PersonalItem) -> str:
    if item.calendar_ref is not None:
        return f"calendar:{item.calendar_ref.provider}"
    return "personal"


def note_template(item: PersonalItem) -> str:
    """ "Sprint planning with @sara, @ali" for meetings with handles in the title/attendees; the plain title otherwise."""
    if item.kind.value == "meeting" and item.attendees:
        handles = [a.split("@", 1)[0] for a in item.attendees[:4]]
        if "with" not in item.title.lower():
            return f"{item.title} with " + ", ".join("@" + h for h in handles)
    return item.title


def build_message(
    item: PersonalItem,
    *,
    actor: str,
    project: str | None,
    dev_project: str = "DEV",
    now: datetime,
    minutes: int | None = None,
    note: str | None = None,
) -> ClientMessage:
    if item.category == Category.PERSONAL:
        raise NotSendable("personal items are never sent")
    if item.deleted_at is not None:
        raise NotSendable("the item was deleted")
    if item.is_sent:
        raise NotSendable("already sent")
    mins = minutes if minutes is not None else item.minutes
    if not mins or mins <= 0:
        raise NotSendable("no duration to report: set one, or run the timer")
    if item.category == Category.DEVELOPMENT:
        target, tags = dev_project, sorted({*item.tags, "development"})
    else:
        chosen = project or item.project_hint
        if not chosen:
            raise NeedsProject("choose a project for this item")
        target, tags = chosen, [t for t in item.tags if t != "development"]
    at = item.effective_end or item.start or now
    ref = item.calendar_ref
    source = ClientSource(
        origin=origin_of(item),
        calendar_ref=CalendarRef(
            provider=ref.provider,
            event_id=ref.event_id,
            series_id=ref.series_id,
            instance_start=ref.instance_start or item.start,
        )
        if ref
        else None,
    )
    text = note if note is not None else note_template(item)
    return ClientMessage(
        actor=actor,
        action=ClientAction.LOG_TIME,
        at=min(at, now),
        client_id=new_id(),
        item=ClientTaskRef(key=item.item_hint) if item.item_hint else ClientTaskRef(title=item.title),
        project=target,
        tags=tags,
        note=text,
        minutes=mins,
        raw_input=f"{item.kind.value} · {_h(mins)} · {text}",
        source=source,
    )


def _h(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h" if h and not m else f"{h} h {m} min" if h else f"{m} min"


@dataclass
class Sent:
    client_id: str
    batch_id: str
    message: ClientMessage


def queue_message(outbox: Outbox, workspace: str, msg: ClientMessage) -> None:
    batch = ClientBatch(workspace=workspace, messages=[msg], client_version=CLIENT_SCHEMA_MINOR)
    outbox.write(batch.model_dump(mode="json", exclude_none=True))


def send_item(
    item: PersonalItem,
    *,
    outbox: Outbox,
    workspace: str,
    actor: str,
    project: str | None,
    dev_project: str,
    now: datetime,
    minutes: int | None = None,
    note: str | None = None,
) -> tuple[PersonalItem, ClientMessage]:
    msg = build_message(
        item, actor=actor, project=project, dev_project=dev_project, now=now, minutes=minutes, note=note
    )
    queue_message(outbox, workspace, msg)
    item.sent_refs.append(SentRef(client_id=msg.client_id, at=now, minutes=msg.minutes, project=msg.project))
    if msg.project and item.category != Category.DEVELOPMENT:
        item.project_hint = msg.project
    return item, msg


def can_unsend(ref: SentRef, now: datetime, window_min: int = 10) -> bool:
    return ref.unsent_at is None and now - ref.at <= timedelta(minutes=window_min)


def unsend(
    item: PersonalItem, *, outbox: Outbox, workspace: str, actor: str, now: datetime, window_min: int = 10
) -> str:
    """Take back the latest send within the window. A batch the server has not received yet is simply discarded from the outbox;
    otherwise a correction (`update` + `corrects`) is queued, which the server turns into `time.unlogged`."""
    live = [r for r in item.sent_refs if r.unsent_at is None]
    if not live:
        raise NotSendable("nothing to unsend")
    ref = live[-1]
    if not can_unsend(ref, now, window_min):
        raise NotSendable(f"too late: Unsend works for {window_min} minutes after sending")
    for entry in outbox.pending():
        if entry.batch_id == ref.client_id:
            outbox.discard(entry)
            ref.unsent_at = now
            return "discarded"
    msg = ClientMessage(
        actor=actor,
        action=ClientAction.UPDATE,
        at=now,
        client_id=new_id(),
        corrects=ref.client_id,
        source=ClientSource(origin=origin_of(item)),
        raw_input="unsend",
    )
    queue_message(outbox, workspace, msg)
    ref.unsent_at = now
    return "correction"


def mirror_timer(
    item: PersonalItem, action: ClientAction, *, actor: str, workspace: str, now: datetime
) -> ClientBatch | None:
    """Phase 15 task 3: when the item is a *sent* work item the server knows (`server_key` learned from the ack), start/pause/done
    on the local timer can be mirrored as ordinary messages. Items the server does not know produce nothing."""
    key = next((r.server_key for r in reversed(item.sent_refs) if r.server_key and r.unsent_at is None), None)
    if key is None or action not in (
        ClientAction.START,
        ClientAction.PAUSE,
        ClientAction.RESUME,
        ClientAction.DONE,
    ):
        return None
    if split_instance_id(item.id) is None and item.category == Category.PERSONAL:
        return None
    msg = ClientMessage(
        actor=actor,
        action=action,
        at=now,
        client_id=new_id(),
        item=ClientTaskRef(key=key),
        source=ClientSource(origin="personal"),
    )
    return ClientBatch(workspace=workspace, messages=[msg], client_version=CLIENT_SCHEMA_MINOR)


def apply_results(items: list[PersonalItem], results: list[dict[str, Any]]) -> list[PersonalItem]:
    """Record the server's keys on the matching sent refs (so the audit list shows `WEB-142` and the timer can be mirrored)."""
    by_client = {r["client_id"]: r for r in results if r.get("client_id")}
    changed: list[PersonalItem] = []
    for item in items:
        dirty = False
        for ref in item.sent_refs:
            res = by_client.get(ref.client_id)
            key = ((res or {}).get("item") or {}).get("key")
            if key and ref.server_key != key:
                ref.server_key, dirty = key, True
                item.item_hint = item.item_hint or key
        if dirty:
            changed.append(item)
    return changed
