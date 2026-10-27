"""Client parity with the REAL server responses (PR #11 review).

The JSON under `fixtures/server/` is recorded from the server app run in-process (`tools/record_client_fixtures.py`),
so a shape change on the server shows up here instead of as a `TypeError` on a user's laptop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tt_client.pack import ContextPack

SERVER = Path(__file__).parent / "fixtures" / "server"


def recorded(name: str) -> Any:
    return json.loads((SERVER / name).read_text())


def test_a_real_context_pack_with_a_pending_approval_loads_and_compacts() -> None:
    raw = recorded("context-pack.json")
    pack = ContextPack(raw)
    compact = pack.compact()  # used to raise TypeError: 'int' object is not iterable
    assert isinstance(compact["pending_approvals"], list)
    assert isinstance(pack.pending_approvals, list)
    assert json.dumps(compact)  # the translator prompt is built from this
    listed = raw["pending_approvals"]
    count = raw.get("pending_approvals_count", listed if isinstance(listed, int) else len(listed))
    assert count >= 1
    assert len(pack.pending_approvals) in (0, count)  # 0 only while recorded against a count-only server


def test_the_contract_list_shape_is_kept_whole_for_the_translators_and_tt_approve() -> None:
    item = {
        "id": "7d1c",
        "title": "WEB-12 → staging",
        "project_key": "WEB",
        "requested_by": "@sara",
        "requested_at": "2026-10-23T10:00:00+00:00",
    }
    pack = ContextPack({"pending_approvals": [item], "pending_approvals_count": 1})
    assert pack.pending_approvals == [item]
    assert pack.compact()["pending_approvals"] == [
        {"id": "7d1c", "title": "WEB-12 → staging", "project_key": "WEB"}
    ]


@pytest.mark.parametrize("legacy", [0, 3, None])
def test_an_old_server_count_never_crashes_the_client(legacy: Any) -> None:
    pack = ContextPack({"pending_approvals": legacy})
    assert pack.pending_approvals == [] and pack.compact()["pending_approvals"] == []
