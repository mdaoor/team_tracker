"""Encrypt the Jira API token and webhook secret at rest. The key comes from `TT_SECRET_KEY` (falls back to the session secret in dev)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from tt_server.settings import get_settings


def _fernet() -> Fernet:
    s = get_settings()
    material = (s.secret_key or s.session_secret).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"tt-jira:" + material).digest()))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
