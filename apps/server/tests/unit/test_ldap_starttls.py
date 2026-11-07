"""Review A5: a failed StartTLS must never fall through to a clear-text bind."""

from __future__ import annotations

from typing import Any

import ldap3
import pytest
from tt_server.integrations.ldap.provider import StartTlsFailed, ldap_provider_from_settings
from tt_server.settings import Settings


class FakeConn:
    def __init__(self, ok: bool) -> None:
        self.ok, self.bound, self.closed = ok, False, False

    def open(self) -> None: ...

    def start_tls(self) -> bool:
        return self.ok

    def unbind(self) -> None:
        self.closed = True


def _provider(monkeypatch: pytest.MonkeyPatch, ok: bool) -> tuple[Any, FakeConn]:
    conn = FakeConn(ok)
    monkeypatch.setattr(ldap3, "Connection", lambda *a, **k: conn)
    s = Settings(ldap_url="ldap://dc.example.test", ldap_base_dn="dc=example,dc=test", ldap_start_tls=True)
    return ldap_provider_from_settings(s), conn


def test_a_failed_starttls_raises_and_closes_the_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    prov, conn = _provider(monkeypatch, ok=False)
    with pytest.raises(StartTlsFailed):
        prov._connect("cn=svc", "secret")
    assert conn.closed


def test_a_successful_starttls_hands_back_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    prov, conn = _provider(monkeypatch, ok=True)
    assert prov._connect("cn=svc", "secret") is conn
