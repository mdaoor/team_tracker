"""OpenID Connect authorization-code flow with PKCE (Entra ID / ADFS / any compliant provider).

The ID token is verified against the provider's JWKS (signature, `iss`, `aud`, `exp`, `nonce`); nothing
from the `userinfo` endpoint is trusted without it. `http` is injectable for tests.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from tt_server.api.errors import ApiError
from tt_server.settings import Settings


@dataclass(frozen=True)
class OidcIdentity:
    subject: str
    email: str
    display_name: str
    handle_hint: str
    groups: frozenset[str]


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OidcProvider:
    name = "oidc"

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str | None,
        redirect_uri: str,
        groups_claim: str = "groups",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id, self.client_secret = client_id, client_secret
        self.redirect_uri, self.groups_claim = redirect_uri, groups_claim
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._meta: dict[str, Any] | None = None

    async def metadata(self) -> dict[str, Any]:
        if self._meta is None:
            r = await self._http.get(f"{self.issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            self._meta = dict(r.json())
        return self._meta

    async def authorization_url(self, *, state: str, nonce: str, challenge: str) -> str:
        meta = await self.metadata()
        q = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(q)}"

    async def identity(self, *, code: str, verifier: str, nonce: str) -> OidcIdentity:
        meta = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        resp = await self._http.post(meta["token_endpoint"], data=data)
        if resp.status_code != 200 or "id_token" not in resp.json():
            raise ApiError(401, "oidc_failed", "the identity provider rejected the login")
        jwks = await self._http.get(meta["jwks_uri"])
        try:
            token = jwt.decode(resp.json()["id_token"], KeySet.import_key_set(jwks.json()))
            registry = jwt.JWTClaimsRegistry(
                iss={"essential": True, "value": meta.get("issuer", self.issuer)},
                aud={"essential": True, "value": self.client_id},
                exp={"essential": True},
                nonce={"essential": True, "value": nonce},
            )
            registry.validate(token.claims)
        except (JoseError, ValueError, KeyError) as exc:
            raise ApiError(401, "oidc_failed", f"invalid id_token: {exc}") from exc
        c = token.claims
        email = str(c.get("email") or c.get("preferred_username") or "")
        if "@" not in email or c.get("email_verified") is False:
            raise ApiError(401, "oidc_failed", "the identity provider did not supply a verified e-mail")
        groups = c.get(self.groups_claim) or []
        return OidcIdentity(
            subject=str(c["sub"]),
            email=email.lower(),
            display_name=str(c.get("name") or email.split("@")[0]),
            handle_hint=str(c.get("preferred_username") or email).split("@")[0],
            groups=frozenset(str(g) for g in groups),
        )


def oidc_provider_from_settings(settings: Settings) -> OidcProvider | None:
    if not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_redirect_uri):
        return None
    return OidcProvider(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=settings.oidc_redirect_uri,
        groups_claim=settings.oidc_groups_claim,
    )
