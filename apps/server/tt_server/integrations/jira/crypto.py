"""Encrypt the Jira API token and webhook secret at rest. The key comes from `TT_SECRET_KEY`; only `TT_ENV=dev` may fall back to the session secret (ADR-0046)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from tt_server.settings import get_settings


def _fernet() -> Fernet:
    s = get_settings()
    if not s.secret_key and s.env != "dev":
        raise RuntimeError("TT_SECRET_KEY is required to encrypt integration credentials outside dev")
    material = (s.secret_key or s.session_secret).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"tt-jira:" + material).digest()))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
