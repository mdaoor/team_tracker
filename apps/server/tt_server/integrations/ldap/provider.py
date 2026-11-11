"""Active Directory over LDAPS: user lookup, password check by simple bind, nested group expansion.

ldap3 is blocking, so every public coroutine hops to a worker thread. `connect` is injectable so tests can run
the whole flow against ldap3's in-memory MOCK_SYNC directory (see `tests/integration/test_ldap.py`).
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import ldap3
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from tt_server.settings import Settings

USER_ATTRS = ["sAMAccountName", "userPrincipalName", "displayName", "mail", "memberOf", "distinguishedName"]
Connect = Callable[[str | None, str | None], Any]  # (bind dn, password) -> bound or bindable ldap3 Connection
MAX_GROUPS = 2000  # circular / pathological nesting guard


@dataclass(frozen=True)
class DirectoryUser:
    dn: str
    sam: str
    upn: str | None
    display_name: str
    email: str | None
    groups: frozenset[str] = field(default_factory=frozenset)  # DNs, nested groups expanded

    @property
    def handle(self) -> str:
        return "@" + self.sam.lower()


def normalise_username(username: str) -> tuple[str, str]:
    """`DOMAIN\\jdoe` / `jdoe@corp.example` / `jdoe` -> (attribute, value) to search on."""
    username = username.strip()
    if "\\" in username:
        return "sAMAccountName", username.split("\\", 1)[1]
    if "@" in username:
        return "userPrincipalName", username
    return "sAMAccountName", username


def _first(values: Any) -> str | None:
    if values in (None, [], ""):
        return None
    if isinstance(values, list | tuple):
        return str(values[0]) if values else None
    return str(values)


class LdapProvider:
    name = "ldap"

    def __init__(
        self, *, base_dn: str, bind_dn: str | None, bind_password: str | None, connect: Connect
    ) -> None:
        self.base_dn, self.bind_dn, self.bind_password, self._connect = (
            base_dn,
            bind_dn,
            bind_password,
            connect,
        )

    # ------------------------------------------------------------------ blocking core
    def _service(self) -> Any:
        conn = self._connect(self.bind_dn, self.bind_password)
        if not conn.bound and not conn.bind():
            raise LDAPException("service account bind failed")
        return conn

    def _find(self, conn: Any, username: str, *, by: str | None = None) -> Any | None:
        attr, value = ("mail", username.strip()) if by == "mail" else normalise_username(username)
        if not value:
            return None
        conn.search(
            self.base_dn,
            f"(&(objectClass=user)({attr}={escape_filter_chars(value)}))",
            attributes=USER_ATTRS,
            size_limit=2,
        )
        entries = list(conn.entries)
        return entries[0] if len(entries) == 1 else None  # ambiguous matches are refused

    def _expand_groups(self, conn: Any, direct: list[str]) -> frozenset[str]:
        """Breadth-first walk over `memberOf` (portable; AD's in-chain matching rule is not needed)."""
        seen: dict[str, str] = {}
        frontier = list(direct)
        while frontier and len(seen) < MAX_GROUPS:
            dn = frontier.pop()
            if dn.lower() in seen:
                continue
            seen[dn.lower()] = dn
            conn.search(dn, "(objectClass=*)", search_scope=ldap3.BASE, attributes=["memberOf"])
            for e in conn.entries:
                frontier.extend(str(g) for g in e.memberOf.values if str(g).lower() not in seen)
        return frozenset(seen.values())

    def _to_user(self, conn: Any, entry: Any) -> DirectoryUser:
        direct = [str(g) for g in entry.memberOf.values] if "memberOf" in entry else []
        return DirectoryUser(
            dn=str(entry.entry_dn),
            sam=_first(entry.sAMAccountName.value) or "",
            upn=_first(entry.userPrincipalName.value) if "userPrincipalName" in entry else None,
            display_name=_first(entry.displayName.value) or _first(entry.sAMAccountName.value) or "",
            email=_first(entry.mail.value) if "mail" in entry else None,
            groups=self._expand_groups(conn, direct),
        )

    def _authenticate(self, username: str, password: str) -> DirectoryUser | None:
        if not password:  # an empty password turns a simple bind into an anonymous one, which AD accepts
            return None
        svc = self._service()
        try:
            entry = self._find(svc, username)
            if entry is None:
                return None
            user = self._to_user(svc, entry)
        finally:
            svc.unbind()
        probe = self._connect(user.dn, password)
        try:
            if not probe.bind():
                return None
        finally:
            probe.unbind()
        return user

    def _lookup(self, username: str, by: str | None = None) -> DirectoryUser | None:
        svc = self._service()
        try:
            entry = self._find(svc, username, by=by)
            return self._to_user(svc, entry) if entry is not None else None
        finally:
            svc.unbind()

    # ------------------------------------------------------------------ async API
    async def authenticate(self, username: str, password: str) -> DirectoryUser | None:
        return await asyncio.to_thread(self._authenticate, username, password)

    async def lookup(self, username: str) -> DirectoryUser | None:
        return await asyncio.to_thread(self._lookup, username)

    async def lookup_email(self, email: str) -> DirectoryUser | None:
        """Re-find a person the way login matched them (by e-mail), whatever handle they ended up with."""
        return await asyncio.to_thread(self._lookup, email, "mail")


class StartTlsFailed(RuntimeError):
    """StartTLS was required but not established: never send a password over the clear connection."""


def ldap_provider_from_settings(settings: Settings) -> LdapProvider | None:
    if not (settings.ldap_url and settings.ldap_base_dn):
        return None
    url = settings.ldap_url
    tls = ldap3.Tls(
        validate=ssl.CERT_REQUIRED,
        ca_certs_file=settings.ldap_ca_bundle,
        version=ssl.PROTOCOL_TLS_CLIENT,
    )
    server = ldap3.Server(
        url,
        use_ssl=url.lower().startswith("ldaps://"),
        tls=tls,
        get_info=ldap3.NONE,
        connect_timeout=5,
    )

    def connect(dn: str | None, password: str | None) -> Any:
        conn = ldap3.Connection(
            server, user=dn, password=password, receive_timeout=10, raise_exceptions=False
        )
        if settings.ldap_start_tls and not url.lower().startswith("ldaps://"):
            conn.open()
            if (
                not conn.start_tls()
            ):  # ldap3 returns False (does not raise) when the upgrade or certificate check fails
                conn.unbind()
                raise StartTlsFailed("LDAP StartTLS failed; refusing to bind over a clear-text connection")
        return conn

    return LdapProvider(
        base_dn=settings.ldap_base_dn,
        bind_dn=settings.ldap_bind_dn,
        bind_password=settings.ldap_bind_password,
        connect=connect,
    )
