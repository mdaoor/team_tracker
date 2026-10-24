"""Concurrent stage moves leave exactly one current placement (DB exclusion constraint + target row lock)."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine, text

from tests.e2e.conftest import Live


async def test_ten_concurrent_moves_leave_one_current_placement(live: Live) -> None:
    base = f"{live.url}/api/v1/w/{live.slug}"
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {live.manage_token}"}) as c:
        created = await c.post(f"{base}/items", json={"title": "contended", "project": "WEB"})
        assert created.status_code == 201, created.text
        key = created.json()["key"]
        stages = [
            "idea",
            "discovery",
            "design",
            "build",
            "test",
            "staging",
            "design",
            "build",
            "test",
            "idea",
        ]
        results = await asyncio.gather(
            *[c.put(f"{base}/items/{key}/stage", json={"stage": s}) for s in stages]
        )
        assert {r.status_code for r in results} <= {200, 409}, [
            r.text for r in results if r.status_code not in (200, 409)
        ]
        history = (await c.get(f"{base}/items/{key}/placements")).json()["data"]
    current = [h for h in history if h["exited_at"] is None]
    assert len(current) == 1
    ordered = sorted(history, key=lambda h: h["entered_at"])
    for a, b in zip(ordered, ordered[1:], strict=False):
        assert (
            a["exited_at"] is not None and a["exited_at"] <= b["entered_at"]
        )  # no overlap in the history chain
    from os import environ

    eng = create_engine(environ["TT_TEST_DATABASE_URL"])
    with eng.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM stage_placements sp JOIN work_items w ON w.id = sp.target_id "
                "JOIN projects p ON p.id = w.project_id JOIN workspaces ws ON ws.id = p.workspace_id "
                "WHERE ws.slug = :s AND w.key = :k AND sp.exited_at IS NULL"
            ),
            {"s": live.slug, "k": key},
        ).scalar()
    eng.dispose()
    assert n == 1
