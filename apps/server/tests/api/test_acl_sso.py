"""ADR-0036: Kerberos/SPNEGO behind a proxy. The principal header is trusted only from configured proxy addresses, maps to a member
by e-mail, never provisions on API calls, and /auth/sso turns it into a session under the same policy as the other sign-ins."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.settings import Settings
from tt_server.sso import normalise, trusted_peer

from tests.api.conftest import Api

ROUTERS_COVERED = ("webauth",)


def configure(api: Api, **kw: object) -> Settings:
    s = api.c._transport.app.state.settings  # type: ignore[attr-defined]
    s.trusted_principal_header = "X-Remote-User"
    s.trusted_proxies = "127.0.0.1/32"
    s.principal_email_domain = "acme.test"
    for k, v in kw.items():
        setattr(s, k, v)
    return s  # type: ignore[no-any-return]


async def test_a_trusted_principal_authenticates_the_api_call_as_that_member(api: Api) -> None:
    configure(api)
    r = await api.c.get(api.url("/me"), headers={"X-Remote-User": "SARA@ACME.TEST"})
    assert r.status_code == 200 and r.json()["handle"] == "@sara"
    r = await api.c.get(
        api.url("/me"), headers={"X-Remote-User": "ali@CORP.LOCAL"}
    )  # realm replaced by the configured domain
    assert r.status_code == 200 and r.json()["handle"] == "@ali"


async def test_the_header_is_ignored_unless_the_peer_is_a_trusted_proxy(api: Api) -> None:
    configure(api, trusted_proxies="10.0.0.0/8")  # the test client connects from 127.0.0.1
    r = await api.c.get(api.url("/me"), headers={"X-Remote-User": "sara@acme.test"})
    assert r.status_code == 401  # forged by a direct client: not a credential
    configure(api, trusted_proxies="")
    assert (await api.c.get(api.url("/me"), headers={"X-Remote-User": "sara@acme.test"})).status_code == 401
    configure(api, trusted_principal_header=None, trusted_proxies="127.0.0.1")
    assert (await api.c.get(api.url("/me"), headers={"X-Remote-User": "sara@acme.test"})).status_code == 401


async def test_unknown_principals_and_hostile_values_are_refused(api: Api) -> None:
    configure(api)
    for value in (
        "nobody@acme.test",
        "sara@acme.test\r\nX-Evil: 1",
        "a b@acme.test",
        "",
        "@acme.test",
        "x" * 300,
    ):
        r = await api.c.get(api.url("/me"), headers={"X-Remote-User": value.replace("\r\n", " ")})
        assert r.status_code == 401, value
    assert (await api.c.get(api.url("/me"), headers={"X-Remote-User": "nobody@acme.test"})).json()["error"][
        "code"
    ] == "unauthorized"


async def test_a_bearer_token_still_wins_over_a_header(api: Api) -> None:
    configure(api)
    r = await api.c.get(
        api.url("/me"), headers={"Authorization": "Bearer nope", "X-Remote-User": "sara@acme.test"}
    )
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"


async def test_sso_login_sets_a_session_and_redirects_only_to_safe_targets(api: Api) -> None:
    configure(api)
    r = await api.c.get(
        "/auth/sso",
        params={"workspace": "acme", "next": "/w/acme/review"},
        headers={"X-Remote-User": "sara@acme.test"},
    )
    assert (
        r.status_code == 302
        and r.headers["location"] == "/w/acme/review"
        and "tt_session" in r.headers.get("set-cookie", "")
    )
    r = await api.c.get(
        "/auth/sso",
        params={"workspace": "acme", "next": "https://evil.example/"},
        headers={"X-Remote-User": "sara@acme.test"},
    )
    assert r.headers["location"] == "/"
    assert (await api.c.get("/auth/sso", params={"workspace": "acme"})).status_code == 401  # no principal
    assert (
        await api.c.get(
            "/auth/sso", params={"workspace": "nope"}, headers={"X-Remote-User": "sara@acme.test"}
        )
    ).status_code == 404
    assert (await api.c.get("/auth/providers")).json()["sso"] is True


async def test_sso_provisions_by_the_workspace_policy_not_automatically(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    configure(api)
    # not a member, domain not allowed: access request + 403
    r = await api.c.get(
        "/auth/sso", params={"workspace": "acme"}, headers={"X-Remote-User": "newbie@acme.test"}
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "pending_approval"
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        ws.settings = {**ws.settings, "auth": {"allowed_domains": ["acme.test"]}}
        await s.commit()
    r = await api.c.get(
        "/auth/sso", params={"workspace": "acme"}, headers={"X-Remote-User": "newbie@acme.test"}
    )
    assert r.status_code == 302
    assert (await api.c.get(api.url("/me"), headers={"X-Remote-User": "newbie@acme.test"})).json()[
        "handle"
    ] == "@newbie"


async def test_groups_header_feeds_the_idp_mapping(api: Api) -> None:
    configure(api, trusted_groups_header="X-Remote-Groups")
    r = await api.c.get(
        "/auth/sso",
        params={"workspace": "acme"},
        headers={"X-Remote-User": "sara@acme.test", "X-Remote-Groups": "TT-Managers, Other"},
    )
    assert r.status_code == 302


@pytest.mark.parametrize(
    ("raw", "domain", "want"),
    [
        ("jdoe@CORP.LOCAL", "example.com", ("jdoe@example.com", "jdoe")),
        ("jdoe@example.org", "example.com", ("jdoe@example.org", "jdoe")),
        ("CORP\\JDoe", "example.com", ("jdoe@example.com", "jdoe")),
        ("jdoe", "example.com", ("jdoe@example.com", "jdoe")),
        ("jdoe@corp.example", None, ("jdoe@corp.example", "jdoe")),
        ("jdoe", None, None),
        ("", "example.com", None),
        ("j doe", "example.com", None),
        ("a;b@x.y", "x.y", None),
        ("@x.y", "x.y", None),
    ],
)
def test_normalise(raw: str, domain: str | None, want: tuple[str, str] | None) -> None:
    assert normalise(raw, domain) == want


def test_trusted_peer_matching() -> None:
    s = Settings(trusted_proxies="10.0.0.0/8, 192.168.1.5, ::1")
    assert trusted_peer(s, "10.2.3.4") and trusted_peer(s, "192.168.1.5") and trusted_peer(s, "::1")
    assert not trusted_peer(s, "192.168.1.6") and not trusted_peer(s, None) and not trusted_peer(s, "garbage")
    assert not trusted_peer(Settings(), "10.2.3.4")
    with pytest.raises(RuntimeError, match="TRUSTED_PROXIES"):
        Settings(trusted_principal_header="X-U").validate_for_runtime()


async def test_dev_login_sets_a_session_only_while_dev_auth_is_on(api: Api) -> None:
    r = await api.c.post("/auth/dev/login", json={"workspace": "acme", "handle": "sara"})
    assert (
        r.status_code == 200
        and r.json()["handle"] == "@sara"
        and "tt_session" in r.headers.get("set-cookie", "")
    )
    assert (
        await api.c.post("/auth/dev/login", json={"workspace": "acme", "handle": "@nobody"})
    ).status_code == 401
    assert (
        await api.c.post("/auth/dev/login", json={"workspace": "zzz", "handle": "@sara"})
    ).status_code == 404
    assert (await api.c.get("/auth/providers")).json()["dev"] is True
    api.c._transport.app.state.settings.dev_auth = False  # type: ignore[attr-defined]
    assert (
        await api.c.post("/auth/dev/login", json={"workspace": "acme", "handle": "@sara"})
    ).status_code == 404
    assert (await api.c.get("/auth/providers")).json()["dev"] is False
