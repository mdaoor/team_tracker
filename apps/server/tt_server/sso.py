"""Kerberos / SPNEGO sign-in behind a reverse proxy (ADR-0036).

The proxy (IIS ARR, nginx + `spnego-http-auth`, Apache `mod_auth_gssapi`, Traefik forward-auth ...) validates the ticket and forwards
the authenticated principal in `TT_TRUSTED_PRINCIPAL_HEADER`. The application trusts that header **only** when the TCP peer is one of
`TT_TRUSTED_PROXIES`; from anyone else it is ignored (and stripped from consideration), so it cannot be forged by a client.
In-process GSSAPI (keytab on the app host) is the alternative recorded as an open question in the ADR."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fastapi import Request

from tt_server.settings import Settings


@dataclass(frozen=True)
class Principal:
    raw: str
    email: str
    handle_hint: str
    groups: frozenset[str]


def _networks(spec: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            out.append(ipaddress.ip_network(part, strict=False))
    return out


def trusted_peer(settings: Settings, peer: str | None) -> bool:
    if not peer or not settings.trusted_proxies.strip():
        return False
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in net for net in _networks(settings.trusted_proxies))


def normalise(raw: str, email_domain: str | None) -> tuple[str, str] | None:
    """`DOMAIN\\jdoe`, `jdoe`, `jdoe@REALM`, `jdoe@example.com` -> (email, handle hint); None when it cannot be mapped to an e-mail."""
    p = raw.strip()
    if not p or len(p) > 256 or any(c in p for c in "\r\n\0 ,;"):
        return None
    if "\\" in p:
        p = p.rsplit("\\", 1)[1]
    local, _, realm = p.partition("@")
    if not local:
        return None
    if email_domain:
        # with a configured domain a Kerberos realm (upper-case) is replaced; an explicit lower-case mail domain is kept
        domain = realm.lower() if realm and not realm.isupper() else email_domain.lower()
    elif realm:
        domain = realm.lower()
    else:
        return None  # a bare account name with no domain to complete it to an e-mail
    return f"{local.lower()}@{domain}", local.lower()


def principal_from(request: Request, settings: Settings) -> Principal | None:
    header = settings.trusted_principal_header
    if not header:
        return None
    peer = request.client.host if request.client else None
    if not trusted_peer(settings, peer):
        return None
    raw = request.headers.get(header)
    if not raw:
        return None
    mapped = normalise(raw, settings.principal_email_domain)
    if mapped is None:
        return None
    groups: frozenset[str] = frozenset()
    if settings.trusted_groups_header and (g := request.headers.get(settings.trusted_groups_header)):
        groups = frozenset(x.strip() for x in g.split(",") if x.strip())
    return Principal(raw, mapped[0], mapped[1], groups)
