"""Outbox delivery in chunks (Phase 14 review M9): the server caps `commands[]` at 50 per batch, so `sync` never sends more."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tt_client.api import Unreachable
from tt_client.outbox import MAX_COMMANDS_PER_BATCH, Outbox, sync


class EchoApi:
    """Applies everything it is sent and remembers every batch; optionally goes offline after `n` posts."""

    def __init__(self, offline_after: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.offline_after = offline_after

    def post_batch(self, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.offline_after is not None and len(self.calls) >= self.offline_after:
            raise Unreachable("down")
        self.calls.append(json.loads(json.dumps(b)))
        ids = [m["client_id"] for m in b.get("messages", [])] + [c["client_id"] for c in b.get("commands", [])]
        return 200, {"results": [{"client_id": i, "status": "applied"} for i in ids]}


def batch_with(n: int, *, message: bool = True) -> dict[str, Any]:
    msgs = (
        [{"actor": "@mahmoud", "action": "start", "at": "2026-10-25T10:00:00+03:00", "client_id": "msg-1"}]
        if message
        else []
    )
    cmds = [
        {
            "client_id": f"cmd-{i:03d}",
            "actor": "@mahmoud",
            "at": "2026-10-25T10:00:00+03:00",
            "op": "tag.create",
            "args": {"name": f"t{i}"},
            "preview_only": False,
        }
        for i in range(n)
    ]
    return {"workspace": "acme", "messages": msgs, "commands": cmds}


def test_the_cap_matches_the_server() -> None:
    assert MAX_COMMANDS_PER_BATCH == 50


def test_a_batch_with_more_than_50_commands_is_sent_in_chunks_of_at_most_50(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.confirm(box.write(batch_with(120)))
    api = EchoApi()
    report = sync(api, box)
    assert [len(c["commands"]) for c in api.calls] == [50, 50, 20]
    assert [len(c["messages"]) for c in api.calls] == [1, 0, 0]  # the message rides with the first chunk only
    sent = [c["client_id"] for call in api.calls for c in call["commands"]]
    assert sent == [f"cmd-{i:03d}" for i in range(120)]  # order kept, nothing repeated or lost
    assert all(c["args"]["_meta"]["confirmed"] for call in api.calls for c in call["commands"])
    assert report.applied_commands == 120 and report.sent == 3 and report.kept == 0 and box.pending() == []


def test_unsent_chunks_stay_queued_and_confirmed_when_the_network_drops_midway(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.confirm(box.write(batch_with(120, message=False)))
    first = EchoApi(offline_after=1)
    report = sync(first, box)
    assert report.offline and len(first.calls) == 1 and len(first.calls[0]["commands"]) == 50
    rest = box.pending()
    assert len(rest) == 2 and all(p.confirmed_at for p in rest)  # the unsent chunks are durable and still confirmed
    later = EchoApi()
    sync(later, box)
    resumed = [c["client_id"] for call in later.calls for c in call["commands"]]
    assert resumed == [f"cmd-{i:03d}" for i in range(50, 120)] and box.pending() == []


def test_an_unconfirmed_oversized_batch_is_neither_sent_nor_split(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.write(batch_with(70, message=False))
    api = EchoApi()
    report = sync(api, box)
    assert api.calls == [] and len(report.unconfirmed or []) == 1 and len(box.pending()) == 1


def test_exactly_50_commands_is_one_post(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.confirm(box.write(batch_with(50)))
    api = EchoApi()
    sync(api, box)
    assert len(api.calls) == 1 and len(api.calls[0]["commands"]) == 50
