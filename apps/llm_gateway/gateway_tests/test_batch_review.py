"""Batch review of outbox files (Phase 2b §2b.4): 10+ scenarios; deterministic merging, a proposal only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gateway_tests.conftest import PACK, Backend, Harness, request_body, text


def msg(cid: str, action: str, at: str, **kw: Any) -> dict[str, Any]:
    return {"actor": "@mahmoud", "action": action, "at": f"2026-10-23T{at}:00+03:00", "client_id": cid, **kw}


def batch(*msgs: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"workspace": "acme", "messages": list(msgs), "client_version": "0.1", **kw}


async def review(h: Harness, *batches: dict[str, Any], pack: dict[str, Any] | None = None) -> dict[str, Any]:
    r = await h.post(
        "/v1/agent/batch-review", request_body(batches=list(batches), pack=PACK if pack is None else pack)
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_start_and_later_log_time_become_one_message(make: Callable[..., Harness]) -> None:
    h = make()
    a = batch(msg("a", "start", "09:00", item={"key": "WEB-110"}))
    b = batch(msg("b", "log_time", "10:30", minutes=90, note="oauth tests"))
    out = await review(h, a, b)
    files = out["output"]["files"]
    assert [f["action"] for f in files] == ["merge", "drop"]
    merged = files[0]["batch"]["messages"][0]
    assert merged["action"] == "start" and merged["minutes"] == 90 and merged["note"] == "oauth tests"
    assert merged["client_id"] == "a" and "merged" in files[0]["changes"][0]
    assert len(out["batches"]) == 1  # what `tt sync --assist` counts: 2 -> 1


async def test_a_fragment_too_far_from_its_start_is_not_merged(make: Callable[..., Harness]) -> None:
    out = await review(
        make(),
        batch(msg("a", "start", "09:00", item={"key": "WEB-110"})),
        batch(msg("b", "log_time", "13:00", minutes=30)),
    )
    assert [f["action"] for f in out["output"]["files"]] == ["keep", "keep"]


async def test_a_fragment_that_names_its_own_item_is_left_alone(make: Callable[..., Harness]) -> None:
    out = await review(
        make(),
        batch(
            msg("a", "start", "09:00", item={"key": "WEB-110"}),
            msg("b", "log_time", "09:30", minutes=20, item={"key": "DATA-12"}),
        ),
    )
    assert (
        out["output"]["files"][0]["action"] == "keep"
        and len(out["output"]["files"][0]["batch"]["messages"]) == 2
    )


async def test_other_peoples_messages_are_never_merged(make: Callable[..., Harness]) -> None:
    other = msg("b", "log_time", "09:30", minutes=20)
    other["actor"] = "@sara"
    out = await review(make(), batch(msg("a", "start", "09:00", item={"key": "WEB-110"})), batch(other))
    assert [f["action"] for f in out["output"]["files"]] == ["keep", "keep"]


async def test_duplicate_creates_within_a_day_keep_one_and_warn(make: Callable[..., Harness]) -> None:
    c1 = msg("c1", "create", "09:00", item={"title": "Write the release notes"})
    c2 = msg("c2", "create", "15:00", item={"title": "write  the release NOTES"})
    out = await review(make(), batch(c1), batch(c2))
    assert [f["action"] for f in out["output"]["files"]] == ["merge", "drop"]
    assert (
        "dropped duplicate c2" in out["warnings"]
        and "duplicate of c1" in out["output"]["files"][1]["changes"][0]
    )


async def test_duplicates_a_day_apart_are_distinct_work(make: Callable[..., Harness]) -> None:
    c1 = msg("c1", "create", "09:00", item={"title": "Standup notes"})
    c2 = dict(msg("c2", "create", "09:00", item={"title": "Standup notes"}), at="2026-10-25T09:00:00+03:00")
    out = await review(make(), batch(c1), batch(c2))
    assert [f["action"] for f in out["output"]["files"]] == ["keep", "keep"]


async def test_project_is_filled_from_aliases_without_a_model(make: Callable[..., Harness]) -> None:
    b = Backend()
    out = await review(
        make(b), batch(msg("a", "create", "09:00", item={"title": "tune the pipeline retries"}))
    )
    f = out["output"]["files"][0]
    assert f["batch"]["messages"][0]["project"] == "DATA" and f["action"] == "merge" and b.calls == []


async def test_project_is_filled_from_the_glossary(make: Callable[..., Harness]) -> None:
    out = await review(make(), batch(msg("a", "note", "09:00", note="the ingest thing is flaky again")))
    assert out["output"]["files"][0]["batch"]["messages"][0]["project"] == "DATA"


async def test_the_model_fills_a_project_only_with_confidence_and_a_known_key(
    make: Callable[..., Harness],
) -> None:
    sure = Backend([text({"project": "WEB", "confidence": 0.9})])
    out = await review(
        make(sure), batch(msg("a", "create", "09:00", item={"title": "make the footer sticky"}))
    )
    assert out["output"]["files"][0]["batch"]["messages"][0]["project"] == "WEB"
    unsure = Backend([text({"project": "WEB", "confidence": 0.4})])
    out = await review(
        make(unsure), batch(msg("a", "create", "09:00", item={"title": "make the footer sticky"}))
    )
    assert out["output"]["files"][0]["batch"]["messages"][0].get("project") is None
    invented = Backend([text({"project": "NOPE", "confidence": 0.99})])
    out = await review(
        make(invented), batch(msg("a", "create", "09:00", item={"title": "make the footer sticky"}))
    )
    assert out["output"]["files"][0]["batch"]["messages"][0].get("project") is None


async def test_a_dead_model_does_not_break_the_review(make: Callable[..., Harness]) -> None:
    dead = Backend(default=lambda kw: (_ for _ in ()).throw(RuntimeError("down")))
    out = await review(
        make(dead), batch(msg("a", "create", "09:00", item={"title": "make the footer sticky"}))
    )
    assert out["output"]["files"][0]["action"] == "keep" and any(
        "gap-fill skipped" in w for w in out["warnings"]
    )


async def test_invalid_files_and_preview_keys_are_handled(make: Callable[..., Harness]) -> None:
    good = batch(msg("a", "note", "09:00", note="x", item={"key": "WEB-110"}))
    good["_preview"] = {"rows": []}
    good["messages"][0]["_hint"] = 1
    out = await review(make(), good, {"nonsense": True})
    assert [f["action"] for f in out["output"]["files"]] == ["keep", "keep"]
    assert out["output"]["files"][1]["batch"] == {"nonsense": True} and any(
        "invalid" in w for w in out["warnings"]
    )
    assert len(out["batches"]) == 2


async def test_clean_files_come_back_unchanged(make: Callable[..., Harness]) -> None:
    b = batch(
        msg("a", "start", "09:00", item={"key": "WEB-110"}),
        msg("b", "done", "11:00", item={"key": "WEB-110"}),
    )
    out = await review(make(), b)
    f = out["output"]["files"][0]
    assert (
        f["action"] == "keep"
        and f["changes"] == []
        and [m["client_id"] for m in f["batch"]["messages"]] == ["a", "b"]
    )


async def test_fragments_inside_one_file_merge_in_place(make: Callable[..., Harness]) -> None:
    b = batch(
        msg("a", "resume", "09:00", item={"key": "DATA-12"}),
        msg("b", "note", "09:20", note="stuck on retries"),
        msg("c", "log_time", "09:40", minutes=45),
    )
    out = await review(make(), b)
    (m,) = out["output"]["files"][0]["batch"]["messages"]
    assert m["minutes"] == 45 and "stuck on retries" in m["note"]
