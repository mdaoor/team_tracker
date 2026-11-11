"""The generated configuration reference must match `Settings` (the committed file is what operators read)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_config_reference_is_current() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_config_reference.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )  # noqa: S603
    assert r.returncode == 0, r.stdout


def test_every_setting_is_documented_with_its_env_name() -> None:
    from tt_server.settings import Settings

    text = (ROOT / "docs" / "ops" / "config-reference.md").read_text()
    for name in Settings.model_fields:
        if name != "features":
            assert f"`TT_{name.upper()}`" in text, name
