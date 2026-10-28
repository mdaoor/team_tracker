"""The sidecar command surface for the desktop and mobile shells (ADR-0038): `tt sidecar <command> '<json>'` -> one JSON document.

The React views never parse, store or send; they call these commands through the `PersonalBridge` (apps/desktop/src/personal). Every
command takes a JSON object and returns a JSON-able dict; failures are `{"error": {"code", "message", ...}}` so a shell never has to
parse stderr. `COMMANDS` is the contract the TypeScript side is tested against (`tests/test_personal_bridge.py` writes the shapes to
`apps/desktop/src/personal/contract.json`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from tt_client.integrations.sync import calendar_overview
from tt_client.personal.model import Category
from tt_client.personal.send import NeedsProject, NotSendable
from tt_client.personal.service import Tracker, TrackerError

Handler = Callable[[Tracker, dict[str, Any], Any], Any]


def _day(p: dict[str, Any]) -> date | None:
    return date.fromisoformat(p["date"]) if p.get("date") else None


def _item_row(t: Tracker, item_id: str) -> dict[str, Any]:
    from tt_client.personal.views import item_row

    item = t.get(item_id)
    return item_row(item, t.now, t.store.get_timer(item.id), t.env.tz)


def _require_api(api: Any) -> Any:
    if api is None:
        raise TrackerError("not logged in: nothing was delivered (the outbox keeps it)")
    return api


COMMANDS: dict[str, Handler] = {
    "personal_day": lambda t, p, _a: t.day(_day(p)),
    "personal_week": lambda t, p, _a: t.week(_day(p)),
    "personal_month": lambda t, p, _a: t.month(p.get("year"), p.get("month")),
    "personal_preview": lambda t, p, _a: {"drafts": t.preview(p["line"])},
    "personal_add": lambda t, p, _a: {"items": [_item_row(t, i.id) for i in t.add(p["line"])]},
    "personal_recur_add": lambda t, p, _a: _recur_add(t, p),
    "personal_recur_list": lambda t, p, _a: {
        "series": [
            {
                "id": m.id,
                "title": m.title,
                "rule": t.describe_rule(m),
                "rrule": m.rrule,
                "category": m.category.value,
                "send_each": m.send_each_instance,
            }
            for m in t.store.series()
        ]
    },
    "personal_recur_end": lambda t, p, _a: {
        "item": _item_row(t, t.end_series(p["id"], date.fromisoformat(p["until"])).id)
    },
    "personal_complete": lambda t, p, _a: {"item": _item_row(t, t.complete(p["id"], p.get("minutes")).id)},
    "personal_skip": lambda t, p, _a: {"item": _item_row(t, t.skip(p["id"]).id)},
    "personal_reopen": lambda t, p, _a: {"item": _item_row(t, t.reopen(p["id"]).id)},
    "personal_delete": lambda t, p, _a: {"deleted": t.delete(p["id"])},
    "personal_reschedule": lambda t, p, _a: {
        "item": _item_row(t, t.reschedule(p["id"], datetime.fromisoformat(p["start"])).id)
    },
    "personal_set_category": lambda t, p, _a: {
        "item": _item_row(t, t.set_category(p["id"], Category(p["category"])).id)
    },
    "personal_timer_start": lambda t, p, _a: t.timer_start(p.get("id"), title=p.get("title")),
    "personal_timer_stop": lambda t, p, _a: {"stopped": t.timer_stop()},
    "personal_timer_status": lambda t, p, _a: {"timer": t.timer_status()},
    "personal_tray": lambda t, p, _a: {"entries": t.tray_rows()},
    "personal_tray_notifications": lambda t, p, _a: _notifications(t),
    "personal_send": lambda t, p, _a: t.send(
        p["id"], project=p.get("project"), note=p.get("note"), minutes=p.get("minutes")
    ),
    "personal_not_work": lambda t, p, _a: {"item": _item_row(t, t.not_work(p["id"]).id)},
    "personal_unsend": lambda t, p, _a: {"how": t.unsend(p["id"])},
    "personal_remember_share": lambda t, p, _a: _remember(t, p),
    "personal_eod": lambda t, p, _a: t.end_of_day(_day(p)),
    "personal_eod_send_all": lambda t, p, _a: t.send_all_unsent(_day(p)),
    "personal_eod_confirm": lambda t, p, a: t.confirm_daily_log(_require_api(a), _day(p)),
    "personal_weekly_review": lambda t, p, _a: t.weekly_review(_day(p)),
    "personal_development_summary": lambda t, p, _a: t.development_summary(_day(p)),
    "personal_deliver": lambda t, p, a: t.deliver(_require_api(a)),
    "personal_privacy": lambda t, p, _a: t.privacy(),
    "personal_first_run": lambda t, p, _a: t.first_run(
        work=p.get("work", "ask"),
        meeting=p.get("meeting", "ask"),
        development=bool(p.get("development", False)),
        assist=bool(p.get("assist", False)),
    ),
    "personal_set_assist": lambda t, p, _a: t.set_assist(bool(p.get("on", False))),
    "personal_sent": lambda t, p, _a: {"sent": t.sent_audit()},
    "personal_erase_local": lambda t, p, _a: t.erase_local(),
    "personal_settings": lambda t, p, _a: t.settings.all(),
    "personal_setting_set": lambda t, p, _a: _setting(t, p),
    "personal_cal_sync": lambda t, p, a: _cal_sync(t, p),
    "personal_cal_settings": lambda t, p, _a: calendar_overview(t.store),
    "personal_cal_exclude": lambda t, p, _a: _cal_prefs(t, p),
    "personal_cal_suppress": lambda t, p, _a: _cal_suppress(t, p),
    "personal_cal_refresh": lambda t, p, _a: _cal_refresh(t, p),
}


def _remember(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    t.remember_share(Category(p["category"]), p["mode"])
    return t.privacy()


def _setting(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    t.settings.set(p["key"], p["value"])
    return t.settings.all()


def _recur_add(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    item = t.add_recurring(p["text"])
    if p.get("send_each"):
        t.set_series_send(item.id, each_instance=True)
    return {
        "id": item.id,
        "title": item.title,
        "rule": t.describe_rule(item),
        "rrule": item.rrule,
        "category": item.category.value,
    }


def _notifications(t: Tracker) -> dict[str, Any]:
    from tt_client.personal.tray import due_notifications

    fresh = due_notifications(t.store, t.tray())
    return {"entries": [e.as_dict() for e in fresh]}


def _cal_providers(t: Tracker) -> dict[str, Any]:
    from tt_client.integrations.factory import build_providers

    cfg = t.env.extra.get("cfg")
    provs = build_providers(cfg.extra.get("calendar", {}), t.env.secrets) if cfg else {}
    if not provs:
        raise TrackerError("no calendar configured")
    return provs


def _cal_prefs(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    """`{calendar, excluded: bool}`: switch one calendar off or on; answers the new overview."""
    sync = t.calendar({})
    out = sync.exclude(p["calendar"]) if p.get("excluded", True) else sync.include(p["calendar"])
    return {**calendar_overview(t.store), "changed": out}


def _cal_suppress(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    t.settings.suppress(p["kind"], p["value"], remove=bool(p.get("remove")))
    return calendar_overview(t.store)


def _cal_refresh(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    sync = t.calendar(_cal_providers(t))
    names = [p["provider"]] if p.get("provider") else list(sync.providers)
    return {"refreshed": [sync.refresh_calendars(n) for n in names], **calendar_overview(t.store)}


def _cal_sync(t: Tracker, p: dict[str, Any]) -> dict[str, Any]:
    provs = _cal_providers(t)
    sync = t.calendar(provs)
    return {
        "reports": [
            sync.sync(n, full=bool(p.get("full"))).as_dict()
            for n in ([p["provider"]] if p.get("provider") else list(provs))
        ]
    }


def handle(
    t: Tracker, command: str, payload: dict[str, Any] | None = None, api: Any = None
) -> dict[str, Any]:
    fn = COMMANDS.get(command)
    if fn is None:
        return {"error": {"code": "unknown_command", "message": command}}
    try:
        out = fn(t, payload or {}, api)
        return dict(out) if isinstance(out, dict) else {"result": out}
    except NeedsProject as exc:
        return {
            "error": {
                "code": "needs_project",
                "message": str(exc),
                "alternatives": t.hints().project_keys[:3],
            }
        }
    except NotSendable as exc:
        return {"error": {"code": "not_sendable", "message": str(exc)}}
    except (TrackerError, ValueError, KeyError) as exc:
        return {"error": {"code": "bad_request", "message": str(exc)}}
