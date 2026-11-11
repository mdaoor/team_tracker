"""Run the Procrastinate worker: `python -m tt_worker`."""

import asyncio

from tt_server.jobs.app import app, load_tasks
from tt_server.jobs.engine import dispose_shared
from tt_server.settings import get_settings
from tt_server.telemetry import configure_telemetry


async def _run() -> None:
    load_tasks()
    configure_telemetry("tt-worker", get_settings().otlp_endpoint)
    try:
        async with app.open_async():
            await app.run_worker_async(concurrency=get_settings().worker_concurrency)
    finally:
        await dispose_shared()


if __name__ == "__main__":
    asyncio.run(_run())
