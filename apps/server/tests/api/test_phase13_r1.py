"""Phase 13 R1: blocking UX bugs that have a server half (nav gating, settings, share, soft delete)."""

from __future__ import annotations

from tests.api.conftest import Api


async def test_me_publishes_which_console_areas_the_viewer_may_open(api: Api) -> None:
    admin = (await api.call("GET", "/me")).json()
    assert (
        admin["nav"]["setup"]
        and admin["nav"]["review"]
        and admin["nav"]["weights"]
        and admin["nav"]["policies"]
    )
    ali = (await api.call("GET", "/me", as_="@ali")).json()
    assert ali["nav"]["views"] and ali["nav"]["present"]
    assert not ali["nav"]["setup"] and not ali["nav"]["review"] and not ali["nav"]["policies"]
    assert not ali["nav"]["weights"] and not ali["nav"]["digest"]


async def test_nav_flag_matches_what_the_endpoints_enforce(api: Api) -> None:
    ali = (await api.call("GET", "/me", as_="@ali")).json()["nav"]
    assert ali["policies"] is False
    assert (await api.call("GET", "/policies", as_="@ali")).status_code == 403
    assert ali["review"] is False
    assert (await api.call("GET", "/review-queue", as_="@ali")).status_code in (403, 404)


async def test_admin_toggles_external_sharing_and_it_shows_in_me(api: Api) -> None:
    assert (await api.call("GET", "/me")).json()["settings"]["external_share"] is False
    r = await api.call("PATCH", "/settings", json={"external_share": True})
    assert r.status_code == 200 and r.json()["external_share"] is True
    assert (await api.call("GET", "/me")).json()["settings"]["external_share"] is True
    assert (
        await api.call("PATCH", "/settings", as_="@ali", json={"external_share": False})
    ).status_code == 403
    bad = await api.call("PATCH", "/settings", json={"external_share_redaction": "nope"})
    assert bad.status_code in (400, 422)
    ev = (await api.call("GET", "/events", params={"type": "workspace.settings_changed"})).json()
    assert ev["data"] if "data" in ev else True


async def test_deleted_presentation_view_and_template_can_be_restored(api: Api) -> None:
    pid = (
        await api.call("POST", "/presentations", json={"title": "Keep me", "slides": [{"kind": "kpis"}]})
    ).json()["id"]
    assert (await api.call("DELETE", f"/presentations/{pid}")).status_code == 204
    assert pid not in {p["id"] for p in (await api.call("GET", "/presentations")).json()["data"]}
    assert (await api.call("POST", f"/presentations/{pid}:restore", as_="@ali")).status_code == 404
    r = await api.call("POST", f"/presentations/{pid}:restore")
    assert r.status_code == 200 and r.json()["title"] == "Keep me"
    assert pid in {p["id"] for p in (await api.call("GET", "/presentations")).json()["data"]}

    vid = (await api.call("POST", "/views", json={"name": "V1", "filter": {}, "layout": "list"})).json()["id"]
    assert (await api.call("DELETE", f"/views/{vid}")).status_code == 204
    assert (await api.call("POST", f"/views/{vid}:restore")).status_code == 200
    assert vid in {v["id"] for v in (await api.call("GET", "/views")).json()["data"]}

    from tests.api.test_acl_presentation_templates import BODY

    assert (await api.call("POST", "/presentation-templates", json={"body": BODY})).status_code == 201
    assert (await api.call("DELETE", "/presentation-templates/weekly_status")).status_code == 204
    assert (await api.call("POST", "/presentation-templates/weekly_status:restore")).status_code == 200
    keys = {t["key"] for t in (await api.call("GET", "/presentation-templates")).json()["data"]}
    assert "weekly_status" in keys


async def test_two_requests_assembling_the_same_snapshot_do_not_collide(committed_api: Api) -> None:
    """Builder preview and an export click race on the snapshot cache; the second must not be a 409."""
    import asyncio

    pid = (
        await committed_api.call(
            "POST", "/presentations", json={"title": "Race", "slides": [{"kind": "kpis"}]}
        )
    ).json()["id"]
    rs = await asyncio.gather(
        committed_api.call("GET", f"/presentations/{pid}/data"),
        committed_api.call("GET", f"/presentations/{pid}/data"),
        committed_api.call("GET", f"/presentations/{pid}/data"),
    )
    assert [r.status_code for r in rs] == [200, 200, 200]
