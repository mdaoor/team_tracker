from __future__ import annotations

import httpx
import pytest

from tests.api.conftest import Api


@pytest.fixture
def sapi(seed_client: httpx.AsyncClient) -> Api:
    """API helper bound to the seeded `acme` workspace (default actor @mahmoud)."""
    return Api(seed_client, "acme")
