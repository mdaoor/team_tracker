from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from tt_server.main import create_app
from tt_server.settings import Settings


async def test_readyz_503_when_database_is_down() -> None:
    url = "postgresql+psycopg://tt:tt@127.0.0.1:1/none"
    engine = create_async_engine(url)
    app = create_app(Settings(database_url=url, log_json=False, log_level="CRITICAL"), engine=engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/readyz")
    await engine.dispose()
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
