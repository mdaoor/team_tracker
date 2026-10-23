from __future__ import annotations

from datetime import datetime
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
