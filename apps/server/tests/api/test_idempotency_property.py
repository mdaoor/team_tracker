from __future__ import annotations

import random

from sqlalchemy import func, select
from tt_server.db import models as m

from tests.api.conftest import Api

ACTIONS = [
    "start",
    "pause",
    "resume",
    "done",
    "blocked",
    "note",
    "evidence",
    "estimate",
    "plan",
    "create",
    "update",
    "log_time",
]


async def test_replaying_random_batches_changes_nothing(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(42)
    messages = []
    for i in range(40):
        msg = {
            "actor": rng.choice(["@mahmoud", "@sara"]),
            "action": rng.choice(ACTIONS),
            "client_id": f"prop-{i}",
            "at": f"2026-10-23T{9 + i // 10:02d}:{(i * 7) % 60:02d}:00+03:00",
        }
        if rng.random() < 0.7:
            msg["item"] = {
                "title": rng.choice(["fix login", "write docs", "retry refactor", "deploy pipeline"])
            }
        if rng.random() < 0.4:
            msg["minutes"] = rng.randint(1, 120)
        if rng.random() < 0.3:
            msg["tags"] = [rng.choice(["a", "b", "c"])]
        if rng.random() < 0.3:
            msg["estimate"] = {"value": rng.randint(1, 8), "unit": rng.choice(["hours", "days", "points"])}
        if rng.random() < 0.3:
            msg["note"] = "note"
        if rng.random() < 0.3:
            msg["project"] = rng.choice(["WEB", "DATA", "nope"])
        messages.append(msg)
    body = {"workspace": "acme", "messages": messages}
    first = await api.call("POST", "/capture/batches", json=body)
    assert first.status_code in (200, 207)
    tables = [m.WorkItem, m.Activity, m.TimeEntry, m.Evidence, m.Estimate, m.Event, m.Tag, m.ReviewItem]

    async def snapshot() -> list[int]:
        async with session_factory() as s:
            return [int(await s.scalar(select(func.count()).select_from(t)) or 0) for t in tables]

    before = await snapshot()
    second = await api.call("POST", "/capture/batches", json=body)
    assert await snapshot() == before
    ok = [r for r in first.json()["results"] if r["status"] != "failed"]
    assert len(ok) == 40
    assert all(r["status"] == "deduplicated" for r in second.json()["results"])
