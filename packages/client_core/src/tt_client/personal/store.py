"""The local, encrypted personal store (Phase 15 §15.1, ADR-0041).

SQLite from the standard library. Every row's payload is one Fernet token (AES-128-CBC + HMAC-SHA256) over its JSON, so titles, notes,
attendees and links never touch the disk in clear. Only what an index needs stays readable: ids, the UTC *date* an item starts on, the
series id and a deleted flag. The key lives in the OS keyring (file fallback with a warning, see `tt_client.secrets`).

The schema has its own versioned migrations (`MIGRATIONS`); a database written by a newer client is refused, never downgraded.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel

from tt_client.personal.model import PersonalItem, Rule, Timer
from tt_client.secrets import SecretStore

KEY_NAME = "personal-store-key"
M = TypeVar("M", bound=BaseModel)


class StoreError(Exception):
    pass


class WrongKey(StoreError):
    """The store was written with a different key (keyring entry lost?)."""


# ---------------------------------------------------------------------------------------------------- migrations
def _m1(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE items(id TEXT PRIMARY KEY, day TEXT, series_id TEXT, deleted INTEGER NOT NULL DEFAULT 0, blob BLOB NOT NULL);
        CREATE INDEX items_day ON items(day);
        CREATE INDEX items_series ON items(series_id);
        CREATE TABLE rules(id TEXT PRIMARY KEY, blob BLOB NOT NULL);
        CREATE TABLE timers(item_id TEXT PRIMARY KEY, state TEXT NOT NULL, blob BLOB NOT NULL);
        CREATE TABLE settings(key TEXT PRIMARY KEY, blob BLOB NOT NULL);
        """
    )


def _m2(db: sqlite3.Connection) -> None:
    """Calendar sync state (cursors, chosen calendars) is kept per provider, encrypted like everything else."""
    db.execute("CREATE TABLE calendar_state(provider TEXT PRIMARY KEY, blob BLOB NOT NULL)")


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_m1, _m2]
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(db: sqlite3.Connection) -> int:
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    have = int(row[0] or 0)
    if have > SCHEMA_VERSION:
        raise StoreError(
            f"personal store is version {have}; this client understands up to {SCHEMA_VERSION}. Update the app."
        )
    for version in range(have + 1, SCHEMA_VERSION + 1):
        with db:
            MIGRATIONS[version - 1](db)
            db.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)", (version, datetime.now(UTC).isoformat())
            )
    return SCHEMA_VERSION


# ---------------------------------------------------------------------------------------------------- keys
def load_or_create_key(secrets: SecretStore) -> bytes:
    key = secrets.get(KEY_NAME)
    if key is None:
        key = Fernet.generate_key().decode()
        secrets.set(KEY_NAME, key)
    return key.encode()


# ---------------------------------------------------------------------------------------------------- store
def _day(item: PersonalItem) -> str | None:
    return item.start.astimezone(UTC).date().isoformat() if item.start else None


class PersonalStore:
    """Items, timers, rules, settings. Not thread-safe by design (one store per process; the sidecar serialises calls)."""

    def __init__(self, path: Path | str, key: bytes) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY"
        )
        self._fernet = Fernet(key)
        self.version = migrate(self.db)
        if self.path != ":memory:":
            Path(self.path).chmod(0o600)
        self._check_key()

    @classmethod
    def open(cls, path: Path | str, secrets: SecretStore) -> PersonalStore:
        return cls(path, load_or_create_key(secrets))

    @classmethod
    def memory(cls) -> PersonalStore:
        return cls(":memory:", Fernet.generate_key())

    # -- crypto
    def _enc(self, doc: BaseModel | dict[str, Any]) -> bytes:
        raw = doc.model_dump_json() if isinstance(doc, BaseModel) else json.dumps(doc)
        return self._fernet.encrypt(raw.encode())

    def _dec(self, blob: bytes) -> str:
        try:
            return self._fernet.decrypt(bytes(blob)).decode()
        except InvalidToken as exc:
            raise WrongKey("cannot decrypt the personal store with the key in the keychain") from exc

    def _check_key(self) -> None:
        row = self.db.execute("SELECT blob FROM settings WHERE key = '__canary__'").fetchone()
        if row is None:
            with self.db:
                self.db.execute("INSERT INTO settings VALUES ('__canary__', ?)", (self._enc({"ok": True}),))
        else:
            self._dec(row[0])

    # -- items
    def put_item(self, item: PersonalItem) -> PersonalItem:
        item.updated_at = datetime.now(UTC)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO items(id, day, series_id, deleted, blob) VALUES (?,?,?,?,?)",
                (
                    item.id,
                    _day(item),
                    item.series_id or (item.id if item.rrule else None),
                    int(item.deleted_at is not None),
                    self._enc(item),
                ),
            )
        return item

    def get_item(self, item_id: str, *, include_deleted: bool = False) -> PersonalItem | None:
        row = self.db.execute("SELECT blob FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        item = PersonalItem.model_validate_json(self._dec(row[0]))
        return item if include_deleted or item.deleted_at is None else None

    def items(self, *, include_deleted: bool = False) -> list[PersonalItem]:
        sql = "SELECT blob FROM items" + ("" if include_deleted else " WHERE deleted = 0")  # noqa: S608 - constants only
        return [PersonalItem.model_validate_json(self._dec(r[0])) for r in self.db.execute(sql)]

    def items_on_days(self, first: date, last: date) -> list[PersonalItem]:
        """One-off items whose UTC start date is within [first-1, last+1] (callers filter exactly; the margin covers zones)."""
        lo, hi = (first - timedelta(days=1)).isoformat(), (last + timedelta(days=1)).isoformat()
        rows = self.db.execute("SELECT blob FROM items WHERE deleted = 0 AND day BETWEEN ? AND ?", (lo, hi))
        return [PersonalItem.model_validate_json(self._dec(r[0])) for r in rows]

    def series(self) -> list[PersonalItem]:
        return [i for i in self.items() if i.is_recurring]

    def overrides_for(self, series_id: str) -> list[PersonalItem]:
        rows = self.db.execute("SELECT blob FROM items WHERE series_id = ? AND deleted = 0", (series_id,))
        out = [PersonalItem.model_validate_json(self._dec(r[0])) for r in rows]
        return [i for i in out if i.series_id == series_id]

    def delete_item(self, item_id: str) -> bool:
        item = self.get_item(item_id)
        if item is None:
            return False
        item.deleted_at = datetime.now(UTC)
        self.put_item(item)
        return True

    # -- timers
    def put_timer(self, timer: Timer) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO timers(item_id, state, blob) VALUES (?,?,?)",
                (timer.item_id, timer.state, self._enc(timer)),
            )

    def get_timer(self, item_id: str) -> Timer | None:
        row = self.db.execute("SELECT blob FROM timers WHERE item_id = ?", (item_id,)).fetchone()
        return Timer.model_validate_json(self._dec(row[0])) if row else None

    def timers(self) -> list[Timer]:
        return [
            Timer.model_validate_json(self._dec(r[0])) for r in self.db.execute("SELECT blob FROM timers")
        ]

    def running_timer(self) -> Timer | None:
        row = self.db.execute("SELECT blob FROM timers WHERE state = 'running'").fetchone()
        return Timer.model_validate_json(self._dec(row[0])) if row else None

    # -- rules
    def put_rule(self, rule: Rule) -> Rule:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO rules VALUES (?,?)", (rule.id, self._enc(rule)))
        return rule

    def rules(self) -> list[Rule]:
        return [Rule.model_validate_json(self._dec(r[0])) for r in self.db.execute("SELECT blob FROM rules")]

    def delete_rule(self, rule_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))

    # -- settings and per-provider state
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT blob FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(self._dec(row[0]))["v"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, self._enc({"v": value})))

    def settings(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT key, blob FROM settings WHERE key != '__canary__'").fetchall()
        return {k: json.loads(self._dec(b))["v"] for k, b in rows}

    def get_calendar_state(self, provider: str) -> dict[str, Any]:
        row = self.db.execute("SELECT blob FROM calendar_state WHERE provider = ?", (provider,)).fetchone()
        return dict(json.loads(self._dec(row[0]))) if row else {}

    def set_calendar_state(self, provider: str, state: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO calendar_state VALUES (?,?)", (provider, self._enc(state))
            )

    def delete_calendar_state(self, provider: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM calendar_state WHERE provider = ?", (provider,))

    # -- export / import (JSON backup, device move). The export is plaintext on purpose: the person asked for it.
    def export_doc(self) -> dict[str, Any]:
        return {
            "format": "tt-personal-export",
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "items": [i.model_dump(mode="json") for i in self.items(include_deleted=True)],
            "rules": [r.model_dump(mode="json") for r in self.rules()],
            "timers": [t.model_dump(mode="json") for t in self.timers()],
            "settings": self.settings(),
        }

    def import_doc(self, doc: dict[str, Any], *, replace: bool = False) -> dict[str, int]:
        if doc.get("format") != "tt-personal-export" or int(doc.get("version", 0)) != 1:
            raise StoreError("not a Team Tracker personal export (format/version)")
        items = [PersonalItem.model_validate(d) for d in doc.get("items", [])]
        rules = [Rule.model_validate(d) for d in doc.get("rules", [])]
        timers = [Timer.model_validate(d) for d in doc.get("timers", [])]
        if replace:
            self.wipe()
        counts = {"items": 0, "rules": 0, "timers": 0}
        for it in items:
            have = self.get_item(it.id, include_deleted=True)
            if have is None or have.updated_at <= it.updated_at:  # newest wins when merging two devices
                stamp = it.updated_at
                self.put_item(it)
                self._set_updated(it.id, stamp)
                counts["items"] += 1
        for r in rules:
            self.put_rule(r)
            counts["rules"] += 1
        for t in timers:
            self.put_timer(t)
            counts["timers"] += 1
        for k, v in (doc.get("settings") or {}).items():
            self.set_setting(k, v)
        return counts

    def _set_updated(self, item_id: str, stamp: datetime) -> None:
        item = self.get_item(item_id, include_deleted=True)
        if item is None:
            return
        item.updated_at = stamp
        with self.db:
            self.db.execute("UPDATE items SET blob = ? WHERE id = ?", (self._enc(item), item_id))

    # -- erase
    def wipe(self) -> None:
        with self.db:
            for table in ("items", "rules", "timers", "calendar_state"):
                self.db.execute(f"DELETE FROM {table}")  # noqa: S608 - a fixed list of our own tables
            self.db.execute("DELETE FROM settings WHERE key != '__canary__'")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.db:
            yield self.db


def db_files(path: Path) -> Iterable[Path]:
    """The database and its WAL/SHM siblings (erase removes all of them)."""
    return [
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    ]
