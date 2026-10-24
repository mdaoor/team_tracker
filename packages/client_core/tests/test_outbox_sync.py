from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tt_client.api import Unreachable
from tt_client.outbox import Outbox, sync
from tt_schema.client import ClientAction, ClientBatch, ClientMessage


def batch(client_id: str = "c-1", **kw: Any) -> dict[str, Any]:
    msg = ClientMessage(
        actor="@mahmoud",
        action=ClientAction.START,
        at="2026-10-23T10:00:00+03:00",  # type: ignore[arg-type]
        client_id=client_id,
        **kw,
    )
    return ClientBatch(workspace="acme", messages=[msg]).model_dump(mode="json")


class FakeApi:
    def __init__(self, *responses: Any) -> None:
        self.responses, self.calls = list(responses), []

    def post_batch(self, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(b)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]


def ok(*ids: str) -> tuple[int, dict[str, Any]]:
    return 200, {"results": [{"client_id": i, "status": "applied"} for i in ids]}


def test_write_pending_and_sent(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    e = box.write(batch("a"), preview={"cards": []})
    assert e.path.exists() and "_preview" in json.loads(e.path.read_text())
    assert [x.batch_id for x in box.pending()] == ["a"]
    assert "_preview" not in box.load(e)
    report = sync(FakeApi(ok("a")), box)
    assert (report.sent, report.kept) == (1, 0) and not e.path.exists()
    assert (box.sent_dir / e.path.name).exists()


def test_offline_keeps_everything_and_delivers_exactly_once_later(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.write(batch("a"))
    box.write(batch("b"))
    api = FakeApi(Unreachable("down"))
    report = sync(api, box)
    assert report.offline and report.kept == 2 and len(api.calls) == 1  # stops at the first failure
    assert box.pending()[0].attempts == 1 and box.pending()[0].last_error.startswith("offline")  # type: ignore[union-attr]
    later = FakeApi(ok("a"), ok("b"))
    assert sync(later, box).sent == 2 and box.pending() == []
    assert [c["messages"][0]["client_id"] for c in later.calls] == ["a", "b"]  # oldest first
    assert sync(FakeApi(), box).sent == 0


def test_schema_violation_is_quarantined(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    e = box.write(batch("bad"))
    report = sync(FakeApi((422, {"error": {"code": "schema_violation"}})), box)
    assert report.quarantined == 1 and box.pending() == []
    assert (box.quarantine_dir / e.path.name).exists() and (box.quarantine_dir / e.path.name).with_suffix(
        ".err"
    ).exists()


def test_207_keeps_only_failed_messages(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    two = batch("ok-1")
    two["messages"].append({**two["messages"][0], "client_id": "bad-1"})
    box.write(two)
    box.db.execute("UPDATE pending SET client_id = 'ok-1'")
    resp = (
        207,
        {
            "results": [
                {"client_id": "ok-1", "status": "applied"},
                {"client_id": "bad-1", "status": "failed", "error_code": "unknown_actor"},
            ]
        },
    )
    report = sync(FakeApi(resp), box)
    assert report.failed_messages == 1
    entry = box.pending()[0]
    assert [m["client_id"] for m in box.load(entry)["messages"]] == ["bad-1"]
    assert entry.last_error and "unknown_actor" in entry.last_error


def test_http_error_keeps_the_batch(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.write(batch("a"))
    report = sync(FakeApi((500, {"error": "boom"})), box)
    assert report.kept == 1 and box.pending()[0].attempts == 1


def test_validate_and_find(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    e = box.write(batch("abc123"))
    assert box.find("abc").batch_id == "abc123" and box.find(str(e.path)).batch_id == "abc123"
    with pytest.raises(KeyError):
        box.find("zzz")
    good = box.load(e)
    assert box.validate(good).messages[0].client_id == "abc123"
    good["messages"][0]["action"] = "teleport"
    with pytest.raises(ValueError, match="action"):
        box.validate(good)


def test_edit_in_place_then_send_uses_the_edited_file(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    e = box.write(batch("a", minutes=120))
    doc = json.loads(e.path.read_text())
    doc["messages"][0]["minutes"] = 90
    e.path.write_text(json.dumps(doc))
    api = FakeApi(ok("a"))
    sync(api, box)
    assert api.calls[0]["messages"][0]["minutes"] == 90


def test_adopting_an_external_file(tmp_path: Path) -> None:
    box = Outbox(tmp_path / "data")
    ext = tmp_path / "draft.json"
    ext.write_text(json.dumps(batch("ext-1")))
    assert box.find(str(ext)).batch_id == "ext-1"
