from __future__ import annotations

from pathlib import Path

import pytest
from tt_client.config import ClientConfig


def test_roundtrip_and_permissions(tmp_path: Path) -> None:
    cfg = ClientConfig(
        server_url="https://tt.example",
        workspace="acme",
        handle="@m",
        token="tt_secret",
        ca_bundle="/etc/tt/ca.pem",
        gateway_url="http://localhost:8800",
        llm_mode="agent",
        feedback=True,
        default_mode="manual",
    )
    path = cfg.save(tmp_path / "config.toml")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    back = ClientConfig.load(path)
    assert (back.server_url, back.workspace, back.token, back.ca_bundle) == (
        "https://tt.example",
        "acme",
        "tt_secret",
        "/etc/tt/ca.pem",
    )
    assert (back.gateway_url, back.llm_mode, back.feedback, back.default_mode) == (
        "http://localhost:8800",
        "agent",
        True,
        "manual",
    )


def test_defaults_and_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ClientConfig.load(tmp_path / "missing.toml").default_mode == "assisted"
    monkeypatch.setenv("TT_TOKEN", "tt_env")
    monkeypatch.setenv("TT_CA_BUNDLE", "/tmp/ca.pem")
    cfg = ClientConfig.load(tmp_path / "missing.toml")
    assert cfg.token == "tt_env" and cfg.ca_bundle == "/tmp/ca.pem"
