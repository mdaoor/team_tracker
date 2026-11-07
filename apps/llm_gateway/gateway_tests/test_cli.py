"""`tt-llm serve` is how the Dockerfile, the Helm chart and the systemd unit start the gateway."""

from typing import Any

import pytest
from tt_llm import main
from typer.testing import CliRunner


def test_serve_subcommand_starts_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kw: calls.append(kw))
    result = CliRunner().invoke(main.cli, ["serve", "--local-only", "--port", "8799"])
    assert result.exit_code == 0, result.output
    assert calls == [{"host": "127.0.0.1", "port": 8799, "ssl_certfile": None, "ssl_keyfile": None}]
