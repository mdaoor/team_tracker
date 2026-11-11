from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tt_client.pack import ContextPack
from tt_client.parser.structured import ParseContext

NOW = datetime.fromisoformat("2026-10-23T12:00:00+00:00")  # Friday 15:00 in Riyadh


@pytest.fixture
def ctx() -> ParseContext:
    return ParseContext(actor="@mahmoud", now=NOW, tz="Asia/Riyadh")


@pytest.fixture
def pack() -> ContextPack:
    return ContextPack(
        {
            "context_schema_version": 1,
            "actor": "@mahmoud",
            "workspace": "acme",
            "projects": [
                {"key": "WEB", "name": "Website", "aliases": ["site"], "jira": "WEB"},
                {"key": "DATA", "name": "Ingestion", "aliases": ["the pipeline"], "jira": None},
            ],
            "my_open_items": [{"key": "WEB-142", "title": "fix the login redirect loop", "project": "WEB"}],
            "people": [{"handle": "@sara", "name": "Sara"}, {"handle": "@ali", "name": "Ali"}],
            "tags": ["auth", "bug"],
            "milestones": [{"name": "Beta", "project": "WEB"}],
            "iterations": [{"name": "Sprint 14", "project": "WEB"}],
            "risks": [{"key": "WEB-R7"}],
            "stages": {},
            "priorities": ["low", "high"],
            "manager_rules": [],
            "glossary": {"the pipeline": "PROJECT:DATA"},
            "manual_mode": {"required_fields": ["action", "item", "project"], "per_project": {}},
        }
    )


@pytest.fixture
def tmp_data(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------------------------------- Phase 15 fixtures
class FakeClock:
    """A settable clock: `clock()` returns the current instant, `advance(minutes=5)` moves it."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> datetime:
        self.now = self.now + timedelta(**kw)
        return self.now


PERSONAL_PACK = {
    "context_schema_version": 1,
    "actor": "@mahmoud",
    "workspace": "acme",
    "projects": [
        {"key": "WEB", "name": "Website", "aliases": ["site"]},
        {"key": "DATA", "name": "Ingestion", "aliases": ["the pipeline"]},
        {"key": "OPS", "name": "Operations", "aliases": []},
    ],
    "my_open_items": [{"key": "WEB-142", "title": "fix the login redirect loop", "project": "WEB"}],
    "people": [
        {"handle": "@sara", "name": "Sara", "team": "web-squad"},
        {"handle": "@ali", "name": "Ali", "team": "web-squad"},
        {"handle": "@omar", "name": "Omar", "team": "platform"},
    ],
    "iterations": [{"name": "Sprint 14", "project": "WEB", "state": "active"}],
    "glossary": {"the pipeline": "PROJECT:DATA", "ingestion": "PROJECT:DATA"},
    "development_project": {"key": "DEV", "tag": "development", "exists": False},
    "calendar_mapping_hints": {
        "teams": [
            {"team": "web-squad", "projects": ["WEB"]},
            {"team": "platform", "projects": ["DATA", "OPS"]},
        ],
        "client_domains": [
            {"domain": "globex.com", "project": "DATA"},
            {"domain": "contoso.com", "project": "WEB"},
        ],
    },
    "my_capacity_hours_per_week": 32.0,
}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def tracker(tmp_path: Path, clock: FakeClock):  # type: ignore[no-untyped-def]
    from tt_client.personal.service import Env, Tracker
    from tt_client.secrets import MemoryStore

    env = Env(
        data_dir=tmp_path,
        secrets=MemoryStore(),
        tz="Asia/Riyadh",
        workspace="acme",
        actor="@mahmoud",
        email="mahmoud@acme.com",
        clock=clock,
        pack=lambda: PERSONAL_PACK,
    )
    return Tracker(env)
