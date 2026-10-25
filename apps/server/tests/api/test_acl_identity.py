"""Sign-in (LDAP/AD, OIDC), sessions + CSRF, group mapping and sync, browser CLI login (Phase 4 §4.5-4.6).

Routers: webauth, identity. The directory is ldap3's in-memory MOCK_SYNC server; the OIDC provider is an
`httpx.MockTransport` that signs real RS256 ID tokens, so signature / issuer / audience / nonce checks are exercised.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from ldap3 import MOCK_SYNC, Connection, Server
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.integrations.ldap import LdapProvider
from tt_server.integrations.oidc import OidcProvider

from tests.api.conftest import Api
from tests.api.test_acl_core import team

ROUTERS_COVERED = ("webauth", "identity")
BASE = "dc=corp,dc=test"
LEADS = f"cn=TT-Platform-Leads,ou=Groups,{BASE}"
ALL = f"cn=TT-All,ou=Groups,{BASE}"


def _user(sam: str, groups: list[str], mail: str | None = None, pw: str = "S3cret!pw") -> dict[str, Any]:
    return {
        "objectClass": "user",
        "sAMAccountName": sam,
        "userPrincipalName": f"{sam}@corp.test",
        "displayName": sam.title(),
        "mail": mail or f"{sam}@corp.test",
        "userPassword": pw,
        "memberOf": groups,
    }


class Directory:
    def __init__(self) -> None:
        self.server = Server("mock-ad")
        self.conn = Connection(self.server, user=f"cn=svc,{BASE}", password="svc", client_strategy=MOCK_SYNC)
        add = self.conn.strategy.add_entry
        add(f"cn=svc,{BASE}", {"objectClass": "user", "userPassword": "svc"})
        add(LEADS, {"objectClass": "group", "memberOf": [ALL]})
        add(ALL, {"objectClass": "group", "memberOf": []})
        add(f"cn=Platform Dev,ou=Groups,{BASE}", {"objectClass": "group", "memberOf": [LEADS]})
        self.add_user("jdoe", [LEADS])
        self.add_user("nested", [f"cn=Platform Dev,ou=Groups,{BASE}"])
        self.add_user("nobody", [])

    def add_user(self, sam: str, groups: list[str], **kw: Any) -> None:
        self.conn.strategy.add_entry(f"cn={sam},ou=Users,{BASE}", _user(sam, groups, **kw))

    def set_groups(self, sam: str, groups: list[str]) -> None:
        dn = f"cn={sam},ou=Users,{BASE}"
        self.conn.strategy.remove_entry(dn)
        self.conn.strategy.add_entry(dn, _user(sam, groups))

    def provider(self) -> LdapProvider:
        def connect(dn: str | None, pw: str | None) -> Connection:
            return Connection(self.server, user=dn, password=pw, client_strategy=MOCK_SYNC)

        return LdapProvider(base_dn=BASE, bind_dn=f"cn=svc,{BASE}", bind_password="svc", connect=connect)


@pytest.fixture
def directory() -> Directory:
    return Directory()


def _app(api: Api) -> Any:
    return api.c._transport.app  # type: ignore[attr-defined]


@pytest.fixture
async def ad(api: Api, directory: Directory) -> Directory:
    _app(api).state.ldap = directory.provider()
    plat = await team(api, "platform")
    r = await api.call(
        "POST",
        "/idp/mappings",
        json={
            "provider": "ldap",
            "group": LEADS,
            "role": "manager",
            "scope_type": "team",
            "scope_id": plat,
            "team": plat,
        },
    )
    assert r.status_code == 201, r.text
    return directory


async def _login(api: Api, user: str, password: str = "S3cret!pw") -> httpx.Response:
    return await api.c.post(
        "/auth/ldap/login", json={"workspace": "acme", "username": user, "password": password}
    )


async def _assignments(s: AsyncSession, person: str) -> set[tuple[str, str]]:
    rows = await s.execute(
        select(m.Role.key, m.RoleAssignment.source)
        .join(m.Role, m.Role.id == m.RoleAssignment.role_id)
        .where(m.RoleAssignment.person_id == person, m.RoleAssignment.deleted_at.is_(None))
    )
    return {(k, src) for k, src in rows.all()}


async def _person(s: AsyncSession, handle: str) -> str:
    return str(await s.scalar(select(m.WorkspaceMember.person_id).where(m.WorkspaceMember.handle == handle)))


# ------------------------------------------------------------------ LDAP login + group mapping
async def test_ldap_login_provisions_member_maps_groups_and_sets_a_session(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    r = await _login(api, "CORP\\jdoe")  # NT-style name
    assert r.status_code == 200, r.text
    assert r.json()["handle"] == "@jdoe"
    assert "tt_session" in r.cookies
    async with session_factory() as s:
        person = await _person(s, "@jdoe")
        assert ("manager", "idp:ldap") in await _assignments(s, person)
        assert ("contributor", "manual") in await _assignments(s, person)
        member_of = (await s.scalars(select(m.Membership).where(m.Membership.person_id == person))).all()
        assert [mb.source for mb in member_of] == ["idp:ldap"]
    me = (await api.c.get("/auth/me")).json()
    assert me["workspaces"] == [{"slug": "acme", "handle": "@jdoe"}]
    # the cookie alone authenticates API reads (no debug header) ...
    assert (await api.c.get(api.url("/items"))).status_code == 200
    # ... and writes need the double-submit CSRF token
    batch = {
        "workspace": "acme",
        "messages": [
            {
                "actor": "@jdoe",
                "action": "create",
                "at": "2026-10-23T10:00:00Z",
                "client_id": "web-1",
                "item": {"title": "from a browser"},
                "project": "nowhere",
            }
        ],
    }
    url = api.url("/capture/batches")
    missing = await api.c.post(url, json=batch)
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "csrf_failed"
    bad = await api.c.post(url, json=batch, headers={"X-CSRF-Token": "forged"})
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "csrf_failed"
    ok = await api.c.post(url, json=batch, headers={"X-CSRF-Token": api.c.cookies.get("tt_csrf") or ""})
    assert ok.status_code == 200, ok.text


async def test_nested_groups_are_expanded(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await _login(api, "nested")).status_code == 200  # Platform Dev -> TT-Platform-Leads
    async with session_factory() as s:
        assert ("manager", "idp:ldap") in await _assignments(s, await _person(s, "@nested"))


async def test_bad_credentials_empty_passwords_and_throttling(api: Api, ad: Directory) -> None:
    assert (await _login(api, "jdoe", "wrong")).status_code == 401
    assert (await _login(api, "ghost")).status_code == 401
    assert (await _login(api, "jdoe", "")).status_code == 400  # rejected before the directory is asked
    assert await ad.provider().authenticate("jdoe", "") is None  # an empty simple bind would be anonymous
    for _ in range(4):
        await _login(api, "jdoe", "wrong")
    locked = await _login(api, "jdoe", "S3cret!pw")
    assert locked.status_code == 429


async def test_unmapped_user_is_held_for_approval_until_the_domain_is_allowed(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    r = await _login(api, "nobody")
    assert r.status_code == 403 and r.json()["error"]["code"] == "pending_approval"
    async with session_factory() as s:
        reqs = (await s.scalars(select(m.ReviewItem).where(m.ReviewItem.kind == "access_request"))).all()
        assert len(reqs) == 1
    await _login(api, "nobody")
    async with session_factory() as s:  # idempotent: still one open request
        assert (
            len((await s.scalars(select(m.ReviewItem).where(m.ReviewItem.kind == "access_request"))).all())
            == 1
        )
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        ws.settings = {**ws.settings, "auth": {"allowed_domains": ["corp.test"]}}
        await s.commit()
    assert (await _login(api, "nobody")).status_code == 200


async def test_handle_collision_gets_a_suffix_and_a_review_row(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    r = await api.call(
        "POST", "/members", json={"display_name": "Other J", "email": "other@acme.test", "handle": "@jdoe"}
    )
    assert r.status_code == 201
    assert (await _login(api, "jdoe")).json()["handle"] == "@jdoe2"
    async with session_factory() as s:
        row = await s.scalar(select(m.ReviewItem).where(m.ReviewItem.kind == "handle_collision"))
        assert row is not None and row.suggestion == {"wanted": "@jdoe", "assigned": "@jdoe2"}


async def test_invited_member_is_activated_on_first_login(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call(
        "POST", "/members", json={"display_name": "Nobody", "email": "nobody@corp.test", "handle": "@nobody"}
    )
    async with session_factory() as s:
        mem = await s.scalar(select(m.WorkspaceMember).where(m.WorkspaceMember.handle == "@nobody"))
        assert mem is not None
        mem.status = "invited"  # type: ignore[assignment]
        await s.commit()
    assert (await _login(api, "nobody")).status_code == 200


async def test_sync_removes_idp_access_but_never_manual_access(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await _login(api, "jdoe")).status_code == 200
    async with session_factory() as s:
        person = await _person(s, "@jdoe")
    plat = (await api.call("GET", "/teams")).json()["data"][0]["id"]
    # an admin also grants jdoe things by hand: a viewer role and a second team membership
    await api.role("@jdoe", "viewer")
    other = await team(api, "ops")
    await api.call("POST", f"/teams/{other}/memberships", json={"person_id": person})
    ad.set_groups("jdoe", [])  # removed from the AD group
    res = await api.call("POST", "/idp/sync")
    assert res.status_code == 200 and res.json()["changed"] == 1, res.text
    async with session_factory() as s:
        assert ("manager", "idp:ldap") not in await _assignments(s, person)
        assert ("viewer", "manual") in await _assignments(s, person)
        teams = {
            mb.team_id
            for mb in (
                await s.scalars(
                    select(m.Membership).where(
                        m.Membership.person_id == person, m.Membership.left_at.is_(None)
                    )
                )
            ).all()
        }
        assert plat not in teams and other in teams
    again = await api.call("POST", "/idp/sync")
    assert again.json()["changed"] == 0  # idempotent


async def test_leaving_the_directory_strips_idp_access_and_sync_needs_ldap(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await _login(api, "jdoe")).status_code == 200
    ad.conn.strategy.remove_entry(f"cn=jdoe,ou=Users,{BASE}")  # account deleted in AD
    res = await api.call("POST", "/idp/sync")
    assert res.status_code == 200 and res.json()["changed"] == 1
    async with session_factory() as s:
        assert ("manager", "idp:ldap") not in await _assignments(s, await _person(s, "@jdoe"))
    _app(api).state.ldap = None
    assert (await api.call("POST", "/idp/sync")).status_code == 409


async def test_mapping_validation_and_permissions(api: Api, ad: Directory) -> None:
    r = await api.call("POST", "/idp/mappings", json={"group": "cn=x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "empty_mapping"
    r = await api.call("POST", "/idp/mappings", json={"group": "cn=x", "role": "nope"})
    assert r.status_code == 404
    r = await api.call(
        "POST",
        "/idp/mappings",
        json={"group": "cn=x", "role": "workspace_admin", "scope_type": "team", "scope_id": "platform"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_scopable"
    assert (await api.call("GET", "/idp/mappings", as_="@ali")).status_code == 403
    assert (await api.call("POST", "/idp/sync", as_="@ali")).status_code == 403
    rows = (await api.call("GET", "/idp/mappings")).json()["data"]
    assert len(rows) == 1
    assert (await api.call("DELETE", f"/idp/mappings/{rows[0]['id']}")).status_code == 204
    assert (await api.call("DELETE", f"/idp/mappings/{rows[0]['id']}")).status_code == 404
    assert (await api.call("GET", "/idp/mappings")).json()["data"] == []


async def test_ldap_token_endpoint_for_the_cli(api: Api, ad: Directory) -> None:
    r = await api.c.post(
        "/auth/ldap/token", json={"workspace": "acme", "username": "jdoe", "password": "S3cret!pw"}
    )
    assert r.status_code == 201
    assert r.json()["scopes"] == ["capture", "read"] and r.json()["handle"] == "@jdoe"
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await api.c.get(api.url("/context-pack"), headers=hdr)).status_code == 200
    assert (
        await api.c.post(api.url("/items"), headers=hdr, json={"title": "x", "project": "WEB"})
    ).status_code == 403


async def test_providers_endpoint_and_unconfigured_flows(api: Api) -> None:
    assert (await api.c.get("/auth/providers")).json() == {
        "oidc": False,
        "ldap": False,
        "sso": False,
        "dev": True,
    }
    assert (await _login(api, "x")).status_code == 404
    assert (await api.c.get("/auth/login", params={"workspace": "acme"})).status_code == 404
    assert (await api.c.get("/auth/me")).status_code == 401
    assert (await api.c.post("/auth/logout")).status_code == 204


# ------------------------------------------------------------------ OIDC
class FakeIdp:
    issuer = "https://idp.corp.test"

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})  # published in the JWKS
        self.signing = self.key  # what actually signs (a different key simulates a forged token)
        self.claims: dict[str, Any] = {}
        self.nonce = ""

    def id_token(self, **over: Any) -> str:
        claims = {
            "iss": self.issuer,
            "aud": "tt-web",
            "sub": "u-1",
            "email": "ola@corp.test",
            "email_verified": True,
            "name": "Ola N",
            "preferred_username": "ola@corp.test",
            "groups": ["g-platform"],
            "exp": int(time.time()) + 600,
            "nonce": self.nonce,
            **self.claims,
            **over,
        }
        return jwt.encode({"alg": "RS256", "kid": self.key.kid}, claims, self.signing)

    def transport(self, **token_over: Any) -> httpx.MockTransport:
        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("openid-configuration"):
                return httpx.Response(
                    200,
                    json={
                        "issuer": self.issuer,
                        "authorization_endpoint": f"{self.issuer}/authorize",
                        "token_endpoint": f"{self.issuer}/token",
                        "jwks_uri": f"{self.issuer}/jwks",
                    },
                )
            if path == "/jwks":
                return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
            if path == "/token":
                form = parse_qs(req.content.decode())
                assert form["code_verifier"] and form["grant_type"] == ["authorization_code"]
                return httpx.Response(
                    200, json={"id_token": self.id_token(**token_over), "access_token": "x"}
                )
            return httpx.Response(404)

        return httpx.MockTransport(handler)


async def _oidc_roundtrip(api: Api, idp: FakeIdp, **token_over: Any) -> httpx.Response:
    provider = OidcProvider(
        issuer=idp.issuer,
        client_id="tt-web",
        client_secret="s",
        redirect_uri="http://test/auth/callback",
        http=httpx.AsyncClient(transport=idp.transport(**token_over)),
    )
    _app(api).state.oidc = provider
    start = await api.c.get("/auth/login", params={"workspace": "acme", "next": "/inbox"})
    assert start.status_code == 302
    q = parse_qs(urlparse(start.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"]
    idp.nonce = q["nonce"][0]
    return await api.c.get("/auth/callback", params={"code": "abc", "state": q["state"][0]})


async def test_oidc_login_maps_groups_and_redirects_to_next(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call(
        "POST", "/idp/mappings", json={"provider": "oidc", "group": "g-platform", "role": "manager"}
    )
    idp = FakeIdp()
    done = await _oidc_roundtrip(api, idp)
    assert done.status_code == 302 and done.headers["location"] == "/inbox"
    assert "tt_session" in api.c.cookies
    async with session_factory() as s:
        person = await _person(s, "@ola")
        assert ("manager", "idp:oidc") in await _assignments(s, person)


@pytest.mark.parametrize(
    "over",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.test"},
        {"exp": 1},
        {"nonce": "replayed"},
        {"email_verified": False},
    ],
)
async def test_oidc_rejects_bad_tokens(api: Api, over: dict[str, Any]) -> None:
    await api.call(
        "POST", "/idp/mappings", json={"provider": "oidc", "group": "g-platform", "role": "manager"}
    )
    r = await _oidc_roundtrip(api, FakeIdp(), **over)
    assert r.status_code == 401 and r.json()["error"]["code"] == "oidc_failed"
    assert "tt_session" not in api.c.cookies


async def test_oidc_rejects_a_token_signed_by_another_key(api: Api) -> None:
    await api.call(
        "POST", "/idp/mappings", json={"provider": "oidc", "group": "g-platform", "role": "manager"}
    )
    idp = FakeIdp()
    idp.signing = RSAKey.generate_key(2048, parameters={"kid": "k1"})  # same kid, different key material
    r = await _oidc_roundtrip(api, idp)
    assert r.status_code == 401 and r.json()["error"]["code"] == "oidc_failed"


async def test_oidc_callback_with_a_forged_state_is_refused(api: Api) -> None:
    idp = FakeIdp()
    _app(api).state.oidc = OidcProvider(
        issuer=idp.issuer,
        client_id="tt-web",
        client_secret=None,
        redirect_uri="http://test/auth/callback",
        http=httpx.AsyncClient(transport=idp.transport()),
    )
    await api.c.get("/auth/login", params={"workspace": "acme"})
    forged = await api.c.get("/auth/callback", params={"code": "x", "state": "not-the-state"})
    assert forged.status_code == 400 and forged.json()["error"]["code"] == "invalid_state"
    no_cookie = await httpx.AsyncClient(transport=api.c._transport, base_url="http://test").get(  # type: ignore[attr-defined]
        "/auth/callback", params={"code": "x", "state": "anything"}
    )
    assert no_cookie.status_code == 400


async def test_open_redirects_are_not_followed(api: Api) -> None:
    idp = FakeIdp()
    provider = OidcProvider(
        issuer=idp.issuer,
        client_id="tt-web",
        client_secret=None,
        redirect_uri="http://test/auth/callback",
        http=httpx.AsyncClient(transport=idp.transport()),
    )
    _app(api).state.oidc = provider
    await api.call(
        "POST", "/idp/mappings", json={"provider": "oidc", "group": "g-platform", "role": "manager"}
    )
    start = await api.c.get("/auth/login", params={"workspace": "acme", "next": "https://evil.test/x"})
    q = parse_qs(urlparse(start.headers["location"]).query)
    idp.nonce = q["nonce"][0]
    done = await api.c.get("/auth/callback", params={"code": "c", "state": q["state"][0]})
    assert done.headers["location"] == "/"


# ------------------------------------------------------------------ browser hand-off for the CLI
async def test_cli_browser_login_hands_the_token_to_loopback_only(api: Api, ad: Directory) -> None:
    cb = "http://127.0.0.1:53211/callback"
    q = {"workspace": "acme", "callback": cb, "state": "xyz"}
    assert (await api.c.get("/auth/cli", params={**q, "callback": "https://evil.test/c"})).status_code == 400
    assert (
        await api.c.get("/auth/cli", params={**q, "callback": "http://evil.test:80/c"})
    ).status_code == 400
    assert (await api.c.get("/auth/cli", params=q)).status_code == 401  # not signed in, no OIDC to bounce to
    assert (await _login(api, "jdoe")).status_code == 200
    r = await api.c.get("/auth/cli", params=q)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == cb
    got = parse_qs(loc.query)
    assert got["state"] == ["xyz"] and got["handle"] == ["@jdoe"] and got["token"][0].startswith("tt_")
    hdr = {"Authorization": f"Bearer {got['token'][0]}"}
    assert (await api.c.get(api.url("/context-pack"), headers=hdr)).status_code == 200


async def test_sync_finds_people_by_email_even_when_their_handle_got_a_suffix(
    api: Api, ad: Directory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call(
        "POST", "/members", json={"display_name": "Other", "email": "other@acme.test", "handle": "@jdoe"}
    )
    assert (await _login(api, "jdoe")).json()["handle"] == "@jdoe2"
    res = await api.call("POST", "/idp/sync")
    assert res.status_code == 200 and res.json()["changed"] == 0  # still in the group: nothing is stripped
    async with session_factory() as s:
        assert ("manager", "idp:ldap") in await _assignments(s, await _person(s, "@jdoe2"))
