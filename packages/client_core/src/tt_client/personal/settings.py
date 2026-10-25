"""Typed access to the personal settings kept (encrypted) in the store, with the defaults of plan §15.6."""

from __future__ import annotations

from typing import Any, Literal

from tt_client.personal.model import Category
from tt_client.personal.store import PersonalStore

ShareMode = Literal["ask", "auto", "never"]
DEFAULTS: dict[str, Any] = {
    "share.work": "ask",  # ask once, then remember (the answer is stored here)
    "share.meeting": "ask",  # suggest after each meeting
    "share.development": False,  # off; one toggle sends development items tagged #development
    "share.personal": "never",  # fixed: there is no control for it
    "privacy.first_run_done": False,
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
    "calendar.write_back": False,
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
        self.store.set_setting(key, value)

    def all(self) -> dict[str, Any]:
        return {k: self.get(k) for k in DEFAULTS}

    def share_mode(self, category: Category) -> ShareMode:
        """What the tray does with a category: `ask` (offer), `auto` (send without a tap), `never` (not offered)."""
        if category == Category.PERSONAL:
            return "never"
        if category == Category.DEVELOPMENT:
            return "ask" if self.get("share.development") else "never"
        return self.get(f"share.{category.value}")  # type: ignore[no-any-return]
