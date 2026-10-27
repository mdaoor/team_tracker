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


def _batch_id(batch: dict[str, Any]) -> str:
    """First message's client_id, else the first command's (a commands-only batch is a valid outbox file)."""
    if batch.get("messages"):
        return str(batch["messages"][0]["client_id"])
    if batch.get("commands"):
        return str(batch["commands"][0]["client_id"])
    raise ValueError("empty batch: no messages and no commands")


@dataclass
class Entry:
    batch_id: str
    path: Path
    attempts: int
    last_error: str | None
    created_at: str
    confirmed_at: str | None = (
        None  # set by `Outbox.confirm` once a person said yes (plan_confirmation passed)
    )


class Outbox:
    """`outbox/<ts>-<client_id>.json` files are the source of truth; `queue.db` tracks attempts/errors."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "outbox"
        self.sent_dir = self.dir / "sent"
        self.quarantine_dir = self.dir / "quarantine"
        self.rejected_dir = (
            self.dir / "rejected"
        )  # commands the server refused (ADR-0052): kept for the person to fix
        for d in (self.dir, self.sent_dir, self.quarantine_dir, self.rejected_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "queue.db")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS pending(client_id TEXT PRIMARY KEY, path TEXT NOT NULL, "
            "batch_json TEXT NOT NULL, created_at TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(pending)")}
        if "confirmed_at" not in cols:  # queues written before the confirmation rule are unconfirmed
            self.db.execute("ALTER TABLE pending ADD COLUMN confirmed_at TEXT")
        self.db.commit()

    # -- writing
    def write(self, batch: dict[str, Any], preview: dict[str, Any] | None = None) -> Entry:
        """Persist a batch as a pretty-printed JSON file and register it in the queue."""
        batch_id = _batch_id(batch)
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
            "SELECT client_id, path, attempts, last_error, created_at, confirmed_at "
            "FROM pending ORDER BY created_at, client_id"
        ).fetchall()
        return [Entry(r[0], Path(r[1]), r[2], r[3], r[4], r[5]) for r in rows]

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
    def confirm(self, entry: Entry) -> None:
        """Record that a person confirmed this entry (the CLI, the sidecar and Slack call it after `plan_confirmation`).

        Only a confirmed entry is ever delivered by `sync` when it carries commands, and only then is `_meta.confirmed`
        stamped on them, so `tt sync` can never turn a `--no-send` draft into a sent destructive command."""
        entry.confirmed_at = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE pending SET confirmed_at = ? WHERE client_id = ?", (entry.confirmed_at, entry.batch_id)
        )
        self.db.commit()

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
    rejected_commands: int = 0  # Phase 14: refused by the server; never retried blindly, never block the rest
    applied_commands: int = 0
    quarantined: int = 0
    offline: bool = False
    kept: int = 0
    results: list[dict[str, Any]] | None = None
    unconfirmed: list[Entry] | None = None  # entries with commands nobody confirmed: kept, never sent


def _stamp_confirmed(batch: dict[str, Any]) -> None:
    """Only called for an entry whose `confirmed_at` is set; the server's destructive-op guard reads `_meta.confirmed`."""
    for c in batch.get("commands", []):
        args = c.setdefault("args", {})
        args.setdefault("_meta", {})["confirmed"] = True


def sync(api: Any, outbox: Outbox, only: list[Entry] | None = None) -> SyncReport:
    """Send pending batches oldest-first. Stops at the first network failure; nothing is ever dropped."""
    from tt_client.api import Unreachable

    report = SyncReport(results=[], unconfirmed=[])
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
        if batch.get("commands"):
            if entry.confirmed_at is None:
                report.unconfirmed.append(entry)  # type: ignore[union-attr]
                continue
            _stamp_confirmed(batch)
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
            command_ids = {c["client_id"] for c in batch.get("commands", [])}
            failed = [
                r for r in results if r.get("status") == "failed" and r.get("client_id") not in command_ids
            ]
            rejected = [
                r for r in results if r.get("status") == "rejected" and r.get("client_id") in command_ids
            ]
            report.applied_commands += sum(
                1
                for r in results
                if r.get("client_id") in command_ids and r.get("status") in ("applied", "deduplicated")
            )
            if rejected:
                report.rejected_commands += len(rejected)
                _park_rejected(outbox, entry, batch, rejected)
            if failed:
                report.failed_messages += len(failed)
                outbox.mark_error(entry, json.dumps(failed))
                batch["_done"] = [
                    r["client_id"]
                    for r in results
                    if r.get("status") in ("applied", "applied_with_warnings", "deduplicated", "rejected")
                ]
                _drop_applied(outbox, entry, batch, failed)
            else:
                outbox.mark_sent(entry)
                report.sent += 1
        else:
            outbox.mark_error(entry, f"HTTP {status}: {json.dumps(body)[:300]}")
    report.kept = len(outbox.pending())
    return report


def _drop_applied(outbox: Outbox, entry: Entry, batch: dict[str, Any], failed: list[dict[str, Any]]) -> None:
    """After a 207, keep only what the server did not finish: failed messages and anything it never answered.

    Applied / deduplicated messages and commands are done, rejected commands were parked: a retry never re-sends them."""
    del failed  # the caller already counted them; `batch["_done"]` carries the finished ids
    done: set[str] = set(batch.pop("_done", []))
    batch["messages"] = [m for m in batch["messages"] if m["client_id"] not in done]
    batch["commands"] = [c for c in batch.get("commands", []) if c["client_id"] not in done]
    entry.path.write_text(json.dumps(batch, indent=2, default=str))


def _park_rejected(
    outbox: Outbox, entry: Entry, batch: dict[str, Any], rejected: list[dict[str, Any]]
) -> None:
    """A rejected command is a final answer: write it with the server's reasons next to the outbox, drop it from the queue."""
    by_id = {r["client_id"]: r for r in rejected}
    parked = [
        {"command": c, "rejection": by_id[c["client_id"]]}
        for c in batch.get("commands", [])
        if c["client_id"] in by_id
    ]
    (outbox.rejected_dir / entry.path.name).write_text(json.dumps(parked, indent=2, default=str))
    batch["commands"] = [c for c in batch.get("commands", []) if c["client_id"] not in by_id]
