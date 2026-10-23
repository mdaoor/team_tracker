"""The reporter-owned outbox (Mode A step 3) and the offline queue index (SQLite)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from tt_schema.client import ClientBatch


@dataclass
class Entry:
    batch_id: str
    path: Path
    attempts: int
    last_error: str | None
    created_at: str


class Outbox:
    """`outbox/<ts>-<client_id>.json` files are the source of truth; `queue.db` tracks attempts/errors."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "outbox"
        self.sent_dir = self.dir / "sent"
        self.quarantine_dir = self.dir / "quarantine"
        for d in (self.dir, self.sent_dir, self.quarantine_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "queue.db")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS pending(client_id TEXT PRIMARY KEY, path TEXT NOT NULL, "
            "batch_json TEXT NOT NULL, created_at TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        self.db.commit()

    # -- writing
    def write(self, batch: dict[str, Any], preview: dict[str, Any] | None = None) -> Entry:
        """Persist a batch as a pretty-printed JSON file and register it in the queue."""
        batch_id = batch["messages"][0]["client_id"]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = self.dir / f"{stamp}-{batch_id}.json"
        doc = {**batch, **({"_preview": preview} if preview else {})}
        path.write_text(json.dumps(doc, indent=2, default=str))
        created = datetime.now(UTC).isoformat()
        self.db.execute(
            "INSERT OR REPLACE INTO pending(client_id, path, batch_json, created_at) VALUES (?,?,?,?)",
            (batch_id, str(path), json.dumps(batch, default=str), created),
        )
        self.db.commit()
        return Entry(batch_id, path, 0, None, created)

    # -- reading
    def pending(self) -> list[Entry]:
        rows = self.db.execute(
            "SELECT client_id, path, attempts, last_error, created_at "
            "FROM pending ORDER BY created_at, client_id"
        ).fetchall()
        return [Entry(r[0], Path(r[1]), r[2], r[3], r[4]) for r in rows]

    def find(self, ref: str) -> Entry:
        """Resolve a file path or a (prefix of a) batch id."""
        p = Path(ref)
        if p.exists():
            for e in self.pending():
                if e.path.resolve() == p.resolve():
                    return e
            return self.adopt(p)
        matches = [e for e in self.pending() if e.batch_id.startswith(ref)]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} outbox entries match {ref!r}")
        return matches[0]

    def adopt(self, path: Path) -> Entry:
        batch = self.load_file(path)
        return self.write(batch)

    @staticmethod
    def load_file(path: Path) -> dict[str, Any]:
        doc = json.loads(path.read_text())
        doc.pop("_preview", None)
        return doc  # type: ignore[no-any-return]

    def load(self, entry: Entry) -> dict[str, Any]:
        return self.load_file(entry.path)

    @staticmethod
    def validate(doc: dict[str, Any]) -> ClientBatch:
        """Raises ValueError with a readable message when the file is not a valid ClientBatch."""
        try:
            return ClientBatch.model_validate(doc)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"{'/'.join(str(p) for p in first['loc'])}: {first['msg']}") from exc

    # -- state changes
    def mark_sent(self, entry: Entry) -> Path:
        self.db.execute("DELETE FROM pending WHERE client_id = ?", (entry.batch_id,))
        self.db.commit()
        dest = self.sent_dir / entry.path.name
        if entry.path.exists():
            entry.path.replace(dest)
        entry.path.with_suffix(".err").unlink(missing_ok=True)
        return dest

    def mark_error(self, entry: Entry, error: str) -> None:
        self.db.execute(
            "UPDATE pending SET attempts = attempts + 1, last_error = ? WHERE client_id = ?",
            (error, entry.batch_id),
        )
        self.db.commit()
        entry.path.with_suffix(".err").write_text(error)

    def quarantine(self, entry: Entry, error: str) -> Path:
        """A 422 means a client bug: move the file aside so it is never retried blindly."""
        self.db.execute("DELETE FROM pending WHERE client_id = ?", (entry.batch_id,))
        self.db.commit()
        dest = self.quarantine_dir / entry.path.name
        if entry.path.exists():
            entry.path.replace(dest)
        dest.with_suffix(".err").write_text(error)
        return dest

    def discard(self, entry: Entry) -> None:
        self.db.execute("DELETE FROM pending WHERE client_id = ?", (entry.batch_id,))
        self.db.commit()
        entry.path.unlink(missing_ok=True)
        entry.path.with_suffix(".err").unlink(missing_ok=True)


@dataclass
class SyncReport:
    sent: int = 0
    failed_messages: int = 0
    quarantined: int = 0
    offline: bool = False
    kept: int = 0
    results: list[dict[str, Any]] | None = None


def sync(api: Any, outbox: Outbox, only: list[Entry] | None = None) -> SyncReport:
    """Send pending batches oldest-first. Stops at the first network failure; nothing is ever dropped."""
    from tt_client.api import Unreachable

    report = SyncReport(results=[])
    ids = {e.batch_id for e in only} if only is not None else None
    for entry in outbox.pending():
        if ids is not None and entry.batch_id not in ids:
            continue
        try:
            batch = outbox.load(entry)
        except (OSError, ValueError) as exc:
            outbox.quarantine(entry, f"unreadable outbox file: {exc}")
            report.quarantined += 1
            continue
        try:
            status, body = api.post_batch(batch)
        except Unreachable as exc:
            outbox.mark_error(entry, f"offline: {exc}")
            report.offline = True
            report.kept = len(outbox.pending())
            return report
        if status == 422:
            outbox.quarantine(entry, json.dumps(body))
            report.quarantined += 1
        elif status in (200, 207):
            results = body.get("results", [])
            report.results.extend(results)  # type: ignore[union-attr]
            failed = [r for r in results if r.get("status") == "failed"]
            if failed:
                report.failed_messages += len(failed)
                outbox.mark_error(entry, json.dumps(failed))
                _drop_applied(outbox, entry, batch, failed)
            else:
                outbox.mark_sent(entry)
                report.sent += 1
        else:
            outbox.mark_error(entry, f"HTTP {status}: {json.dumps(body)[:300]}")
    report.kept = len(outbox.pending())
    return report


def _drop_applied(outbox: Outbox, entry: Entry, batch: dict[str, Any], failed: list[dict[str, Any]]) -> None:
    """After a 207, keep only the failed messages so a retry never re-sends applied ones."""
    failed_ids = {f["client_id"] for f in failed}
    batch["messages"] = [m for m in batch["messages"] if m["client_id"] in failed_ids]
    entry.path.write_text(json.dumps(batch, indent=2, default=str))
