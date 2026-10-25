"""The encrypted local store, its migrations, the secret stores and the timer (Phase 15 §15.1)."""

from __future__ import annotations

import json
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from tt_client.personal.model import Category, PersonalItem, Rule, RuleMatch, RuleSet, SharePolicy, Status
from tt_client.personal.store import SCHEMA_VERSION, PersonalStore, StoreError, WrongKey, load_or_create_key
from tt_client.personal.timer import TimerEngine, TimerError
from tt_client.secrets import FileStore, KeyringStore, MemoryStore, default_store

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def item(title: str = "Rust ch.3 secret title", **kw: object) -> PersonalItem:
    return PersonalItem(title=title, start=T0, duration_min=60, **kw)  # type: ignore[arg-type]


def test_rows_are_encrypted_on_disk_but_round_trip(tmp_path: Path) -> None:
    secrets = MemoryStore()
    store = PersonalStore.open(tmp_path / "p.db", secrets)
    it = store.put_item(item(notes="my private notes", attendees=["sara@acme.com"]))
    store.close()
    raw = b"".join(f.read_bytes() for f in tmp_path.glob("p.db*"))
    for needle in (b"Rust ch.3", b"private notes", b"sara@acme.com"):
        assert needle not in raw
    again = PersonalStore.open(tmp_path / "p.db", secrets)
    got = again.get_item(it.id)
    assert got is not None and got.title == "Rust ch.3 secret title" and got.notes == "my private notes"


def test_the_store_file_is_private_to_the_user(tmp_path: Path) -> None:
    PersonalStore.open(tmp_path / "p.db", MemoryStore()).close()
    assert stat.S_IMODE((tmp_path / "p.db").stat().st_mode) == 0o600


def test_a_lost_key_is_a_clear_error_not_garbage(tmp_path: Path) -> None:
    PersonalStore.open(tmp_path / "p.db", MemoryStore()).close()
    with pytest.raises(WrongKey):
        PersonalStore(tmp_path / "p.db", Fernet.generate_key())


def test_migrations_are_versioned_and_a_newer_database_is_refused(tmp_path: Path) -> None:
    store = PersonalStore.open(tmp_path / "p.db", MemoryStore())
    assert store.version == SCHEMA_VERSION == 2
    rows = store.db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [r[0] for r in rows] == [1, 2]
    store.db.execute("INSERT INTO schema_migrations VALUES (99, 'x')")
    store.db.commit()
    store.close()
    with pytest.raises(StoreError, match="version 99"):
        PersonalStore(tmp_path / "p.db", Fernet.generate_key())


def test_migrating_an_old_database_in_place_keeps_its_rows(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    s = PersonalStore(tmp_path / "p.db", key)
    it = s.put_item(item())
    s.db.execute("DROP TABLE calendar_state")
    s.db.execute("DELETE FROM schema_migrations WHERE version = 2")
    s.db.commit()
    s.close()
    s2 = PersonalStore(tmp_path / "p.db", key)
    assert s2.version == 2 and s2.get_item(it.id) is not None
    s2.set_calendar_state("google", {"x": 1})
    assert s2.get_calendar_state("google") == {"x": 1}


def test_key_creation_is_idempotent() -> None:
    secrets = MemoryStore()
    assert load_or_create_key(secrets) == load_or_create_key(secrets)
    assert "personal-store-key" in secrets.names()


def test_soft_delete_hides_items_and_range_query_finds_by_day() -> None:
    s = PersonalStore.memory()
    a = s.put_item(item("a"))
    s.put_item(PersonalItem(title="far", start=T0 + timedelta(days=40)))
    assert {i.title for i in s.items_on_days(T0.date(), T0.date())} == {"a"}
    assert (
        s.delete_item(a.id)
        and s.get_item(a.id) is None
        and s.get_item(a.id, include_deleted=True) is not None
    )
    assert [i.title for i in s.items()] == ["far"]
    assert s.delete_item("nope") is False


def test_a_personal_item_can_never_carry_a_share_policy() -> None:
    it = PersonalItem(
        title="run", category=Category.PERSONAL, share_policy=SharePolicy.AUTO, send_each_instance=True
    )
    assert it.share_policy == SharePolicy.NEVER and it.send_each_instance is False


def test_item_validation() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PersonalItem(title="x", start=datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError, match="end before start"):
        PersonalItem(title="x", start=T0, end=T0 - timedelta(hours=1))
    it = PersonalItem(title="x", start=T0, end=T0 + timedelta(minutes=45))
    assert it.duration_min == 45 and it.minutes == 45 and it.effective_end == T0 + timedelta(minutes=45)


def test_minutes_prefers_attended_then_timer_then_plan() -> None:
    it = PersonalItem(title="m", start=T0, duration_min=60, actual_min=50, attended_min=40)
    assert it.minutes == 40
    it.attended_min = None
    assert it.minutes == 50
    it.actual_min = None
    assert it.minutes == 60


def test_export_import_round_trip_and_newest_wins(tmp_path: Path) -> None:
    a = PersonalStore.memory()
    one = a.put_item(item("one"))
    a.put_rule(Rule(match=RuleMatch(keyword="sync"), set=RuleSet(project_hint="WEB")))
    a.set_setting("share.work", "auto")
    doc = json.loads(json.dumps(a.export_doc()))
    b = PersonalStore.memory()
    counts = b.import_doc(doc)
    assert counts == {"items": 1, "rules": 1, "timers": 0}
    assert b.get_item(one.id).title == "one"  # type: ignore[union-attr]
    assert b.get_setting("share.work") == "auto" and len(b.rules()) == 1
    # a stale copy does not overwrite a newer local edit
    newer = b.get_item(one.id)
    assert newer is not None
    newer.title = "edited later"
    b.put_item(newer)
    b.import_doc(doc)
    assert b.get_item(one.id).title == "edited later"  # type: ignore[union-attr]
    with pytest.raises(StoreError):
        b.import_doc({"format": "other"})


def test_wipe_removes_everything_but_keeps_the_store_usable() -> None:
    s = PersonalStore.memory()
    s.put_item(item())
    s.put_rule(Rule(match=RuleMatch(keyword="x")))
    s.set_setting("a", 1)
    s.wipe()
    assert s.items() == [] and s.rules() == [] and s.settings() == {}
    s.put_item(item())
    assert len(s.items()) == 1


# ---------------------------------------------------------------------------------------------------- secrets
def test_file_store_is_0600_and_warns_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    path = tmp_path / "secrets.json"
    fs = FileStore(path)
    fs.set("a", "1")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert fs.get("a") == "1" and fs.names() == ["a"]
    FileStore(path)
    assert sum("no OS keyring" in r.message for r in caplog.records) == 1
    fs.delete("a")
    assert fs.get("a") is None


class FakeKeyring:
    def __init__(self) -> None:
        self.d: dict[tuple[str, str], str] = {}

    def get_password(self, s: str, n: str) -> str | None:
        return self.d.get((s, n))

    def set_password(self, s: str, n: str, v: str) -> None:
        self.d[(s, n)] = v

    def delete_password(self, s: str, n: str) -> None:
        self.d.pop((s, n))


def test_keyring_store_keeps_an_index_so_erase_can_find_every_secret() -> None:
    pytest.importorskip("keyring")
    fake = FakeKeyring()
    ks = KeyringStore(fake)
    ks.set("oauth:google", "tok")
    ks.set("personal-store-key", "k")
    assert ks.get("oauth:google") == "tok" and ks.names() == ["oauth:google", "personal-store-key"]
    ks.delete("oauth:google")
    ks.delete("never-existed")
    assert ks.names() == ["personal-store-key"]


def test_default_store_falls_back_to_a_file_when_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TT_SECRETS", "file")
    assert default_store(tmp_path).kind == "file"


# ---------------------------------------------------------------------------------------------------- timer
def make_timer(store: PersonalStore) -> tuple[TimerEngine, list[datetime]]:
    now = [T0]
    return TimerEngine(store, lambda: now[0]), now


def test_one_running_timer_starting_another_pauses_the_first() -> None:
    store = PersonalStore.memory()
    eng, now = make_timer(store)
    a, b = store.put_item(item("a")), store.put_item(item("b"))
    eng.start(a.id)
    now[0] += timedelta(minutes=25)
    _, paused = eng.start(b.id)
    assert paused is not None and paused.item_id == a.id and paused.state == "paused"
    assert eng.current().item_id == b.id  # type: ignore[union-attr]
    now[0] += timedelta(minutes=10)
    eng.start(a.id)  # resume a: b pauses
    now[0] += timedelta(minutes=5)
    assert eng.elapsed_min(a.id) == 30 and eng.elapsed_min(b.id) == 10
    assert len([t for t in store.timers() if t.state == "running"]) == 1
    assert store.get_item(a.id).status == Status.ACTIVE  # type: ignore[union-attr]
    assert store.get_item(b.id).status == Status.PLANNED  # type: ignore[union-attr]


def test_stop_records_minutes_on_the_item_and_a_stopped_timer_can_restart() -> None:
    store = PersonalStore.memory()
    eng, now = make_timer(store)
    a = store.put_item(item("a"))
    eng.start(a.id)
    now[0] += timedelta(minutes=42)
    timer, minutes = eng.stop()  # type: ignore[misc]
    assert minutes == 42 and timer.state == "stopped"
    assert store.get_item(a.id).actual_min == 42  # type: ignore[union-attr]
    assert eng.current() is None and eng.stop() is None
    now[0] += timedelta(minutes=5)
    eng.start(a.id)
    now[0] += timedelta(minutes=8)
    assert eng.stop()[1] == 50  # type: ignore[index]


def test_pause_then_stop_uses_the_paused_timer_and_unknown_items_are_refused() -> None:
    store = PersonalStore.memory()
    eng, now = make_timer(store)
    a = store.put_item(item("a"))
    eng.start(a.id)
    now[0] += timedelta(minutes=10)
    assert eng.pause().state == "paused"  # type: ignore[union-attr]
    now[0] += timedelta(minutes=30)  # paused time does not count
    assert eng.stop()[1] == 10  # type: ignore[index]
    with pytest.raises(TimerError):
        eng.start("nope")


def test_timers_survive_a_restart(tmp_path: Path) -> None:
    secrets = MemoryStore()
    s1 = PersonalStore.open(tmp_path / "p.db", secrets)
    a = s1.put_item(item("a"))
    TimerEngine(s1, lambda: T0).start(a.id)
    s1.close()
    s2 = PersonalStore.open(tmp_path / "p.db", secrets)
    t = TimerEngine(s2, lambda: T0 + timedelta(minutes=7)).current()
    assert t is not None and t.item_id == a.id and t.elapsed_min(T0 + timedelta(minutes=7)) == 7


def test_the_sqlite_file_is_a_normal_sqlite_database(tmp_path: Path) -> None:
    PersonalStore.open(tmp_path / "p.db", MemoryStore()).close()
    tables = {
        r[0]
        for r in sqlite3.connect(tmp_path / "p.db").execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"items", "rules", "timers", "settings", "calendar_state", "schema_migrations"} <= tables
