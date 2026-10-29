"""Typed access to the personal settings kept (encrypted) in the store, with the defaults of plan §15.6."""

from __future__ import annotations

import re
from typing import Any, Literal

from tt_client.personal.model import Category
from tt_client.personal.store import PersonalStore

ShareMode = Literal["ask", "auto", "never"]
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_DOMAIN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
DEFAULTS: dict[str, Any] = {
    "share.work": "ask",  # ask once, then remember (the answer is stored here)
    "share.meeting": "ask",  # suggest after each meeting
    "share.development": False,  # off; one toggle sends development items tagged #development
    "share.personal": "never",  # fixed: there is no control for it
    "privacy.first_run_done": False,
    "privacy.assist": "off",  # "on": undecided quick-add lines may be classified by the [llm] gateway (ADR-0053); off = never leaves the device
    "unsend_minutes": 10,
    "tray.delay_min": 2,
    "tray.max_age_days": 14,
    "capacity_hours_per_week": 40.0,
    "workweek": [0, 1, 2, 3, 4],
    "suppress.attendees": [],
    "suppress.domains": [],
    "calendar.skip_declined": True,
    "calendar.skip_cancelled": True,
    "calendar.skip_all_day": True,
    "calendar.skip_private": True,
    "calendar.skip_ooo": True,
    "calendar.skip_focus": True,
    "calendar.poll_minutes": 5,
    "calendar.excluded": [],  # calendar ids the person switched off
    "calendar.refresh_hours": 24,  # re-list the calendars this often (0: only when no list is cached)
    "calendar.write_back": False,
    "notify.eod_time": "17:30",  # the end-of-day OS nudge (HH:MM local, workdays); empty switches it off
}


class Settings:
    def __init__(self, store: PersonalStore) -> None:
        self.store = store

    def get(self, key: str) -> Any:
        return self.store.get_setting(key, DEFAULTS[key])

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting {key!r}")
        if key == "share.personal" and value != "never":
            raise ValueError("personal items can never be shared: there is no such control")
        if key in ("share.work", "share.meeting") and value not in ("ask", "auto", "never"):
            raise ValueError("share mode must be ask, auto or never")
        if key == "privacy.assist" and value not in ("off", "on"):
            raise ValueError("privacy.assist must be off or on")
        if key == "notify.eod_time":
            from tt_client.personal.notify import valid_clock

            if not isinstance(value, str) or not valid_clock(value):
                raise ValueError("notify.eod_time must be HH:MM (24 h) or empty to switch it off")
        if key == "calendar.refresh_hours" and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError("calendar.refresh_hours must be a whole number of hours, 0 or more")
        self.store.set_setting(key, value)

    def suppress(self, kind: Literal["attendee", "domain"], value: str, *, remove: bool = False) -> list[str]:
        """Add or remove one entry of the never-offer lists (`suppress.attendees` / `suppress.domains`); returns the new list."""
        v = value.strip().lower()
        if kind == "attendee":
            if not _EMAIL.fullmatch(v):
                raise ValueError(f"{value!r} is not an e-mail address")
            key = "suppress.attendees"
        elif kind == "domain":
            v = v.removeprefix("@")
            if not _DOMAIN.fullmatch(v):
                raise ValueError(f"{value!r} is not a domain (like example.com)")
            key = "suppress.domains"
        else:
            raise ValueError("suppress kind must be attendee or domain")
        current = [x for x in self.get(key) if x != v]
        new = current if remove else [*current, v]
        self.set(key, sorted(new))
        return sorted(new)

    def all(self) -> dict[str, Any]:
        return {k: self.get(k) for k in DEFAULTS}

    def share_mode(self, category: Category) -> ShareMode:
        """What the tray does with a category: `ask` (offer), `auto` (send without a tap), `never` (not offered)."""
        if category == Category.PERSONAL:
            return "never"
        if category == Category.DEVELOPMENT:
            return "ask" if self.get("share.development") else "never"
        return self.get(f"share.{category.value}")  # type: ignore[no-any-return]
