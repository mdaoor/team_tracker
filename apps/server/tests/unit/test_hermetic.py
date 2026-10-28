"""Phase 13 R3.2: tests do not read a developer's .env or exported TT_* variables."""

import os

from tt_server.settings import Settings

from tests.conftest import _KEEP_ENV


def test_no_tt_environment_leaks_into_tests() -> None:
    assert [k for k in os.environ if k.startswith("TT_") and k not in _KEEP_ENV] == []


def test_settings_do_not_read_dotenv_under_pytest() -> None:
    assert Settings.model_config["env_file"] is None
