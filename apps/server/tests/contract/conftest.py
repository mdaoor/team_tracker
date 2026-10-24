"""A real uvicorn server on a throw-away clone of the seeded database, for OpenAPI contract testing with Schemathesis."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine, text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def live_server(seeded_url: str) -> Iterator[str]:
    base, _, name = seeded_url.rpartition("/")
    clone = f"{name}_contract"
    admin = create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{clone}" TEMPLATE "{name}"'))
    port = _free_port()
    env = {
        **os.environ,
        "TT_DATABASE_URL": f"{base}/{clone}",
        "TT_DEV_AUTH": "true",
        "TT_LOG_LEVEL": "WARNING",
        "TT_LOG_JSON": "false",
        "TT_ATTACHMENT_DIR": f"/tmp/tt-contract-{port}",
    }
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tt_server.main:create_app",
            "--factory",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise RuntimeError("contract server did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(10)
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{clone}" WITH (FORCE)'))
        admin.dispose()
