"""Run the Procrastinate worker: `python -m tt_worker`."""

import asyncio

from tt_server.jobs.app import app, load_tasks


async def _run() -> None:
    load_tasks()
    async with app.open_async():
        await app.run_worker_async()


if __name__ == "__main__":
    asyncio.run(_run())
