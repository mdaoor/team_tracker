"""Record real server responses for the client tests (PR #11 review, client items).

Runs the server app in-process (httpx ASGI, dev auth) against a throwaway database seeded with the `acme` demo
workspace, creates the situations the client must render (a pending approval, review-queue rows, someone working
now, logged time with a draft daily log), and writes the raw JSON to
`packages/client_core/tests/fixtures/server/`. Re-run it when a server response shape changes:

    TT_TEST_DATABASE_URL=postgresql+psycopg://tt:tt@localhost/tt_test uv run --frozen python tools/record_client_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "client_core" / "tests" / "fixtures" / "server"
sys.path[:0] = [str(ROOT), str(ROOT / "apps" / "server")]

import httpx  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from tests.api.conftest import Api  # noqa: E402
from tests.conftest import _admin_engine, _migrate_into, _seed_demo, _with_db  # noqa: E402
from tools.seed.demo import load_snapshot  # noqa: E402
from tt_server.db import models as m  # noqa: E402
from tt_server.main import create_app  # noqa: E402
from tt_server.settings import Settings  # noqa: E402

DB = "tt_fixture_rec"


def _write(name: str, body: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(body, indent=2, sort_keys=True, default=str) + "\n")
    print("wrote", OUT / name)


def prepare() -> str:
    base = os.environ["TT_TEST_DATABASE_URL"]
    admin = _admin_engine(base)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{DB}"'))
    admin.dispose()
    url = _with_db(base, DB)
    _migrate_into(url)
    _seed_demo(url)
    return url


async def main(url: str) -> None:
    eng = create_async_engine(url)
    sf = async_sessionmaker(eng, expire_on_commit=False)
    settings = Settings(
        database_url=url,
        log_json=False,
        log_level="WARNING",
        dev_auth=True,
        rate_capture_per_min=0,
        rate_auth_per_min=0,
    )
    app = create_app(settings, engine=eng, session_factory=sf)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        api = Api(client, "acme")
        # a pending approval for @mahmoud (a stage that requires approval)
        async with sf() as s:
            staging = await s.scalar(select(m.Stage).where(m.Stage.key == "staging"))
            staging.requires_approval = True
            await s.commit()
        key = next(i["key"] for i in load_snapshot()["items"] if i["project"] == "GOV" and i["stage"] == "build")
        r = await api.call("PUT", f"/items/{key}/stage", json={"stage": "staging"})
        assert r.status_code == 200, r.text
        # an inbox line from @sara (review queue) and work in progress now
        await api.send(as_="@sara", action="create", item={"title": "tidy the ingest retry"})
        for who in ("@sara", "@lina", "@youssef"):
            await api.send(
                as_=who,
                action="start",
                item={"title": "fix the login redirect loop"},
                project="WEB",
            )
        # time logged today -> the server drafts the day
        await api.send(
            as_="@mahmoud",
            action="log_time",
            item={"title": "fix the login redirect loop"},
            project="WEB",
            minutes=45,
        )
        _write("context-pack.json", (await api.call("GET", "/context-pack")).json())
        _write("review-queue.json", (await api.call("GET", "/review-queue")).json())
        _write("activities-team-mine.json", (await api.call("GET", "/activities", params={"team": "mine", "status": "active"})).json())
        _write("daily-logs.json", (await api.call("GET", "/daily-logs", params={"day": "2026-10-23"})).json())
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main(prepare()))
