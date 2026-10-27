"""Phase 13 R1.4 / R6.1: review-queue routing, deterministic defaults, undo and "don't learn"."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m

from tests.api.conftest import Api


async def pid(api: Api, handle: str) -> str:
    return next(
        p["person_id"] for p in (await api.call("GET", "/people")).json()["data"] if p["handle"] == handle
    )


async def line(api: Api, person: str, manager: str) -> None:
    r = await api.call(
        "POST",
        "/reporting-lines",
        json={
            "person_id": await pid(api, person),
            "manager_id": await pid(api, manager),
            "kind": "line",
            "is_primary": True,
        },
    )
    assert r.status_code == 201, r.text


async def queue(api: Api, as_: str = "@mahmoud") -> list[dict]:
    return (await api.call("GET", "/review-queue", as_=as_)).json()["data"]


async def test_a_line_goes_to_the_reporters_line_manager_not_back_to_the_reporter(api: Api) -> None:
    # @sara owns DATA *and* reports to @ali: the old rule sent her own lines to herself (UX review 3)
    await line(api, "@sara", "@ali")
    await api.role("@ali", "manager")
    sara_id = await pid(api, "@sara")
    r = await api.call("PATCH", "/projects/DATA", json={"owner_id": sara_id})
    assert r.status_code == 200, r.text
    res = await api.send(
        as_="@sara", action="create", item={"title": "tidy ingest"}, project="DATA", stage="build"
    )
    key = res["item"]["key"]
    assert key in {r["item_key"] for r in await queue(api, "@ali")}
    assert key not in {r["item_key"] for r in await queue(api, "@mahmoud")}
    assert (await api.call("GET", "/review-queue", as_="@sara")).status_code == 403


async def test_every_inbox_item_gets_a_project_row_even_without_a_phrase(api: Api) -> None:
    res = await api.send(as_="@ali", action="create", item={"title": "refactor the ingestion retry"})
    assert res["item"]["project_key"].startswith("INBOX")
    rows = [r for r in await queue(api) if r["item_key"] == res["item"]["key"]]
    assert rows and rows[0]["kind"] == "unclassified_project"
    assert rows[0]["reporter"] == "@ali" and rows[0]["created_at"]


async def test_rows_always_offer_a_primary_choice_from_the_deterministic_ladder(api: Api) -> None:
    # most-used project in 30 days beats nothing
    await api.send(as_="@ali", action="create", item={"title": "first"}, project="DATA")
    await api.send(as_="@ali", action="create", item={"title": "second"}, project="DATA")
    res = await api.send(as_="@ali", action="create", item={"title": "a stray thought"})
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    assert row["suggested"]["choice"] == {"project": "DATA"} and "most-used" in row["suggested"]["reason"]


async def test_glossary_hit_outranks_the_reporters_habit(api: Api) -> None:
    await api.send(as_="@ali", action="create", item={"title": "x"}, project="DATA")
    web = next(p for p in (await api.call("GET", "/projects")).json()["data"] if p["key"] == "WEB")
    g = await api.call(
        "POST",
        "/glossary",
        json={"phrase": "the shop", "resolves_to_type": "project", "resolves_to_id": web["id"]},
    )
    assert g.status_code == 201, g.text
    from tt_server.domain.overlay.defaults import suggest_default  # noqa: F401  (ladder order is unit-tested below)

    res = await api.send(as_="@ali", action="create", item={"title": "y"}, project="somewhere new")
    item_row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    assert item_row["suggested"]["choice"] == {"project": "DATA"}  # phrase differs: habit wins
    res2 = await api.send(as_="@ali", action="create", item={"title": "z"}, project="the shop")
    assert res2["item"]["project_key"] == "WEB"  # the glossary routes it before any review is needed


async def test_resolve_can_be_undone_and_the_learned_phrase_goes_with_it(api: Api) -> None:
    res = await api.send(
        as_="@ali", action="create", item={"title": "tune it"}, project="the ingestion retry"
    )
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    done = (
        await api.call(
            "POST", "/review-queue/resolve", json={"ref": row["ref"], "choice": {"project": "DATA"}}
        )
    ).json()
    assert done["undoable"] and done["learned"]
    pack = (await api.call("GET", "/context-pack")).json()
    assert pack["glossary"].get("the ingestion retry") == "PROJECT:DATA"
    assert (await api.call("POST", "/review-queue/undo", json={"ref": row["ref"]})).status_code == 200
    again = [r for r in await queue(api) if r["ref"] == row["ref"]]
    assert again, "the row is open again"
    assert "the ingestion retry" not in (await api.call("GET", "/context-pack")).json()["glossary"]
    assert (
        await api.call("POST", "/review-queue/undo", json={"ref": row["ref"]})
    ).status_code == 409  # not resolved any more


async def test_dont_learn_removes_the_phrase_but_keeps_the_move(api: Api) -> None:
    res = await api.send(as_="@ali", action="create", item={"title": "tune it"}, project="the retry thing")
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    await api.call("POST", "/review-queue/resolve", json={"ref": row["ref"], "choice": {"project": "DATA"}})
    out = (await api.call("POST", "/review-queue/unlearn", json={"ref": row["ref"]})).json()
    assert out["unlearned"] is True
    assert "the retry thing" not in (await api.call("GET", "/context-pack")).json()["glossary"]
    assert [r for r in await queue(api) if r["ref"] == row["ref"]] == []
    assert (await api.call("POST", "/review-queue/unlearn", json={"ref": row["ref"]})).json()[
        "unlearned"
    ] is False


async def test_resolve_with_learn_false_never_writes_the_glossary(api: Api) -> None:
    res = await api.send(as_="@ali", action="create", item={"title": "one-off"}, project="that odd place")
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    done = (
        await api.call(
            "POST",
            "/review-queue/resolve",
            json={"ref": row["ref"], "choice": {"project": "DATA", "learn": False}},
        )
    ).json()
    assert done["learned"] is False
    assert "that odd place" not in (await api.call("GET", "/context-pack")).json()["glossary"]


async def test_ignore_is_undoable_and_classification_confirm_too(api: Api) -> None:
    res = await api.send(as_="@ali", action="create", item={"title": "meh"}, project="nowhere at all")
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    await api.call("POST", "/review-queue/resolve", json={"ref": row["ref"], "choice": {"ignore": True}})
    assert (await api.call("POST", "/review-queue/undo", json={"ref": row["ref"]})).status_code == 200
    assert any(r["ref"] == row["ref"] for r in await queue(api))


async def test_a_stranger_cannot_undo_someone_elses_row(api: Api) -> None:
    res = await api.send(as_="@ali", action="create", item={"title": "mine"}, project="nowhere")
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    await api.call("POST", "/review-queue/resolve", json={"ref": row["ref"], "choice": {"ignore": True}})
    assert (
        await api.call("POST", "/review-queue/undo", as_="@sara", json={"ref": row["ref"]})
    ).status_code in (403, 404)


async def test_ladder_prefers_glossary_phrase_over_habit(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select
    from tt_server.db import models as m
    from tt_server.domain.overlay.defaults import suggest_project

    await api.send(as_="@ali", action="create", item={"title": "x"}, project="DATA")
    web = next(p for p in (await api.call("GET", "/projects")).json()["data"] if p["key"] == "WEB")
    await api.call(
        "POST",
        "/glossary",
        json={"phrase": "the shop", "resolves_to_type": "project", "resolves_to_id": web["id"]},
    )
    res = await api.send(as_="@ali", action="create", item={"title": "q"})
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == res["item"]["key"]))
        assert ws and item
        hit = await suggest_project(s, ws.id, item, "the shop")
        assert hit and hit["choice"] == {"project": "WEB"} and hit["confidence"] == 0.9
        habit = await suggest_project(s, ws.id, item, "unknown phrase")
        assert habit and habit["choice"] == {"project": "DATA"}


async def test_resolving_an_inbox_row_without_a_project_phrase_still_learns_from_the_title(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    res = await api.send(as_="@ali", action="create", item={"title": "Refactor the ingestion retry loop"})
    row = next(r for r in await queue(api) if r["item_key"] == res["item"]["key"])
    assert row["suggestion"]["phrase"] == "refactor ingestion retry loop"
    done = (
        await api.call(
            "POST", "/review-queue/resolve", json={"ref": row["ref"], "choice": {"project": "DATA"}}
        )
    ).json()
    assert done["learned"] is True and done["undoable"] is True

    async def learned() -> list[m.GlossaryEntry]:
        async with session_factory() as s:
            return list(
                (
                    await s.scalars(
                        select(m.GlossaryEntry).where(
                            m.GlossaryEntry.source == "learned", m.GlossaryEntry.deleted_at.is_(None)
                        )
                    )
                ).all()
            )

    rows = await learned()
    assert [(e.phrase, e.resolves_to_type) for e in rows] == [("refactor ingestion retry loop", "project")]
    data = next(p for p in (await api.call("GET", "/projects")).json()["data"] if p["key"] == "DATA")
    assert rows[0].resolves_to_id == data["id"]
    # "Don't learn" removes the learned row and keeps the move
    assert (await api.call("POST", "/review-queue/unlearn", json={"ref": row["ref"]})).json()[
        "unlearned"
    ] is True
    assert await learned() == []
    async with session_factory() as s:
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.title == "Refactor the ingestion retry loop"))
    assert item is not None and item.project_id == data["id"]
