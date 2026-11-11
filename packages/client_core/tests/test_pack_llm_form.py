from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from tt_client import form
from tt_client.api import Unreachable
from tt_client.feedback import FeedbackLog, diff_batches
from tt_client.pack import ContextPack, PackCache
from tt_client.parser.llm import GatewayTranslator, NullTranslator, postprocess, translate_line
from tt_client.parser.structured import ParseContext
from tt_client.preview import build_preview
from tt_schema.client import ClientAction, ClientBatch, ClientMessage, ClientTaskRef


class PackApi:
    def __init__(self, pack: dict[str, Any] | None, etag: str | None = '"v1"', fail: bool = False) -> None:
        self.pack, self.etag, self.fail, self.seen = pack, etag, fail, []

    def context_pack(self, etag: str | None) -> tuple[dict[str, Any] | None, str | None]:
        self.seen.append(etag)
        if self.fail:
            raise Unreachable("down")
        return (None, etag) if etag == self.etag else (self.pack, self.etag)


def test_pack_cache_etag_and_offline(tmp_path: Path, pack: ContextPack) -> None:
    cache = PackCache(tmp_path / "pack.json")
    api = PackApi(pack.data)
    assert cache.refresh(api).data == pack.data  # type: ignore[union-attr]
    assert not cache.is_stale()
    assert cache.refresh(api, force=True).data == pack.data  # type: ignore[union-attr]
    assert api.seen == [None, '"v1"']  # second call is conditional
    offline = PackApi(None, fail=True)
    assert cache.refresh(offline, force=True).data == pack.data  # type: ignore[union-attr]
    assert cache.is_stale(now=time.time() + 16 * 60)


def test_pack_import_export_roundtrip(tmp_path: Path, pack: ContextPack) -> None:
    src = tmp_path / "pack.json"
    src.write_text(json.dumps(pack.data))
    cache = PackCache(tmp_path / "cache.json")
    assert cache.import_file(src).project_keys == ["WEB", "DATA"]
    out = tmp_path / "out.json"
    cache.export_file(out)
    assert json.loads(out.read_text()) == pack.data
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ValueError):
        cache.import_file(bad)


def test_pack_accessors(pack: ContextPack) -> None:
    assert pack.project_by_alias("the pipeline") == "DATA" and pack.project_by_alias("SITE") == "WEB"
    assert pack.project_by_alias("nope") is None
    assert pack.people_handles == ["@sara", "@ali"] and pack.required_for("WEB") == [
        "action",
        "item",
        "project",
    ]
    assert set(pack.compact()) >= {"projects", "my_open_items", "people", "tags", "manager_rules", "glossary"}


class StubTranslator:
    def __init__(self, batch: ClientBatch | None) -> None:
        self.batch = batch

    def translate(self, line: str, pack: ContextPack | None, ctx: ParseContext) -> ClientBatch | None:
        return self.batch


def llm_batch(**kw: Any) -> ClientBatch:
    msg = ClientMessage(
        actor="@x",
        action=ClientAction.START,
        at="2026-01-01T00:00:00Z",
        client_id="llm",  # type: ignore[arg-type]
        item=ClientTaskRef(title="fix login"),
        **kw,
    )
    return ClientBatch(workspace="acme", messages=[msg])


def test_deterministic_numbers_win_and_unknown_entities_are_dropped(
    ctx: ParseContext, pack: ContextPack
) -> None:
    b = llm_batch(
        minutes=200,
        assignees=[{"person": "@nobody", "role": "owner"}, {"person": "@sara", "role": "owner"}],
        milestone="Imaginary",
        risks=["WEB-R7", "FAKE-9"],
        on_behalf_of="@nobody",
        tags=["wrong"],
    )
    t = translate_line("doing fix login, took 2h #auth", ctx, pack=pack, translator=StubTranslator(b))
    m = t.batch.messages[0]
    assert t.source == "llm"
    assert m.minutes == 120 and m.tags == ["auth"]
    assert [a["person"] for a in m.assignees] == ["@sara"] and m.milestone is None and m.risks == ["WEB-R7"]
    assert (
        m.on_behalf_of is None and m.actor == "@mahmoud" and m.raw_input == "doing fix login, took 2h #auth"
    )
    assert m.client_id != "llm"


@pytest.mark.parametrize(
    "line", ["tell @nobody about it", "doing x for: @ghost", "start y @phantom milestone Atlantis"]
)
def test_translator_never_emits_people_absent_from_the_pack(
    ctx: ParseContext, pack: ContextPack, line: str
) -> None:
    b = llm_batch(
        assignees=[{"person": "@nobody"}, {"person": "@ghost"}, {"person": "@phantom"}],
        on_behalf_of="@ghost",
        milestone="Atlantis",
    )
    t = translate_line(line, ctx, pack=pack, translator=StubTranslator(b))
    known = set(pack.people_handles)
    for m in t.batch.messages:
        assert {a["person"] for a in m.assignees} <= known
        assert m.on_behalf_of is None or m.on_behalf_of in known
        assert m.milestone is None


def test_low_confidence_overlay_fields_are_nulled(ctx: ParseContext, pack: ContextPack) -> None:
    b = llm_batch(priority="high", milestone="Beta", llm_confidence={"priority": 0.4, "milestone": 0.9})
    m = translate_line("doing x", ctx, pack=pack, translator=StubTranslator(b)).batch.messages[0]
    assert m.priority is None and m.milestone == "Beta"


def test_hallucinated_item_key_is_dropped_but_typed_keys_survive(
    ctx: ParseContext, pack: ContextPack
) -> None:
    b = ClientBatch(
        workspace="a",
        messages=[
            ClientMessage(
                actor="@x",
                action=ClientAction.START,
                at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
                client_id="1",
                item=ClientTaskRef(key="WEB-999", title="t"),
            )
        ],
    )
    assert (
        translate_line("doing t", ctx, pack=pack, translator=StubTranslator(b)).batch.messages[0].item.key
        is None
    )  # type: ignore[union-attr]
    typed = translate_line("doing WEB-999 t", ctx, pack=pack, translator=StubTranslator(b))
    assert typed.batch.messages[0].item.key == "WEB-999"  # type: ignore[union-attr]


def test_falls_back_to_structured_when_the_model_is_unavailable(ctx: ParseContext, pack: ContextPack) -> None:
    t = translate_line("doing fix login, took 1h", ctx, pack=pack, translator=StubTranslator(None))
    assert t.source == "structured" and t.batch.messages[0].minutes == 60
    assert any("model unavailable" in n for n in t.notes)
    assert translate_line("doing x", ctx, pack=None, translator=NullTranslator()).source == "structured"
    assert translate_line("doing x", ctx, pack=None, translator=None, mode="manual").batch.mode == "manual"


def test_gateway_translator_http_behaviour(ctx: ParseContext, pack: ContextPack) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/translate"):
            return httpx.Response(200, json={"batch": llm_batch().model_dump(mode="json")})
        return httpx.Response(500)

    t = GatewayTranslator("http://gw", client=httpx.Client(transport=httpx.MockTransport(handler)))
    got = t.translate("doing fix login", pack, ctx)
    assert got is not None and got.messages[0].item.title == "fix login"  # type: ignore[union-attr]
    body = json.loads(seen[0].content)
    assert body["pack"]["projects"][0]["key"] == "WEB" and "stages" not in body["pack"]
    agent = GatewayTranslator(
        "http://gw", mode="agent", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert agent.translate("x", pack, ctx) is None  # 500 -> None, caller falls back
    assert seen[-1].url.path == "/v1/agent/capture"

    def garbage(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"batch": {"nope": 1}})

    assert (
        GatewayTranslator("http://gw", client=httpx.Client(transport=httpx.MockTransport(garbage))).translate(
            "x", pack, ctx
        )
        is None
    )


def test_preview_rows_and_cues(ctx: ParseContext, pack: ContextPack) -> None:
    msg = ClientMessage(
        actor="@m",
        action=ClientAction.LOG_TIME,
        at="2026-10-23T10:00:00Z",
        client_id="1",  # type: ignore[arg-type]
        item=ClientTaskRef(key="WEB-142"),
        project="site",
        minutes=125,
        tags=["auth", "new"],
        planned_end="2026-10-24T17:00:00+03:00",
        llm_confidence={"project": 0.92},
    )  # type: ignore[arg-type]
    rows = {r.label: r for r in build_preview(msg, pack)}
    assert rows["Task"].cue == "✓ matched" and "WEB-142" in rows["Task"].value
    assert rows["Project"].cue == "✓ matched" and rows["Time"].value == "2 h 5 min logged"
    assert rows["Tags"].cue == "suggested" and "17:00" in rows["Plan"].value
    bare = build_preview(
        ClientMessage(
            actor="@m",
            action=ClientAction.NOTE,
            at="2026-10-23T10:00:00Z",
            client_id="2",  # type: ignore[arg-type]
            note="hello",
        ),
        None,
    )
    assert {r.label: r.cue for r in bare}["Project"] == "will be sorted by your manager"


def test_feedback_diff_and_log(tmp_path: Path) -> None:
    old = {
        "messages": [
            {
                "actor": "@m",
                "action": "log_time",
                "at": "2026-10-23T10:00:00Z",
                "client_id": "1",
                "minutes": 120,
                "raw_input": "took 2h",
            }
        ]
    }
    new = json.loads(json.dumps(old))
    new["messages"][0]["minutes"] = 90
    diff = diff_batches(old, new)
    assert diff == [
        {"client_id": "1", "field": "minutes", "raw": "took 2h", "previous": 120, "corrected": 90}
    ]
    log = FeedbackLog(tmp_path / "fb.jsonl")
    log.append(diff)
    assert log.drain() == diff and log.drain() == []


def test_form_required_fields_and_choices(pack: ContextPack) -> None:
    assert form.missing_required({"action": "start"}, pack) == ["item", "project"]
    assert form.missing_required({"action": "log_time", "item": "x", "project": "WEB"}, pack) == ["minutes"]
    assert form.missing_required({"action": "create", "item": "x", "project": "WEB"}, pack) == ["item_type"]
    assert form.missing_required({"action": "start", "item": "x", "project": "WEB"}, pack) == []
    ch = form.choices(pack)
    assert (
        ch["project"] == ["WEB", "DATA"]
        and ch["assignees"] == ["@sara", "@ali"]
        and ch["milestone"] == ["Beta"]
    )
    assert ch["tags"] == ["auth", "bug"] and ch["item"] == ["WEB-142  fix the login redirect loop"]
    custom = ContextPack(
        {
            **pack.data,
            "manual_mode": {
                "required_fields": ["action", "item", "project", "tags"],
                "per_project": {"DATA": ["action"]},
            },
        }
    )
    assert form.missing_required({"action": "start", "item": "x", "project": "WEB"}, custom) == ["tags"]
    assert form.missing_required({"action": "start", "project": "DATA"}, custom) == []


def test_form_builds_valid_batch_and_roundtrips(ctx: ParseContext, pack: ContextPack) -> None:
    answers = {
        "action": "log_time",
        "item": "WEB-142  fix the login redirect loop",
        "project": "WEB",
        "minutes": "1h30m",
        "tags": ["auth"],
        "priority": "high",
        "planned_end": "tomorrow 5pm",
        "estimate": "3d",
        "assignees": ["@sara"],
        "evidence": ["https://x.test/1"],
        "jira": True,
    }
    msg = form.build_message(answers, actor="@mahmoud", now=ctx.now, tz=ctx.tz)
    assert msg.minutes == 90 and msg.item.key == "WEB-142" and msg.estimate == {"value": 3.0, "unit": "days"}  # type: ignore[union-attr]
    assert msg.planned_end.isoformat() == "2026-10-24T17:00:00+03:00" and msg.jira == {
        "project": "WEB",
        "send": True,
    }  # type: ignore[union-attr]
    batch = form.to_batch([msg], "acme")
    assert batch.mode == "manual" and ClientBatch.model_validate(batch.model_dump(mode="json"))
    back = form.message_to_answers(msg)
    again = form.build_message(back, actor="@mahmoud", now=ctx.now, tz=ctx.tz)
    for f in (
        "action",
        "item",
        "project",
        "minutes",
        "tags",
        "priority",
        "planned_end",
        "assignees",
        "jira",
        "evidence",
    ):
        assert getattr(again, f) == getattr(msg, f), f
    assert again.estimate == {"value": 3.0, "unit": "days"}


def test_postprocess_is_pure_on_structured_only_batches(ctx: ParseContext, pack: ContextPack) -> None:
    b, notes = postprocess(llm_batch(), [], pack, ctx, "line")
    assert notes == [] and b.messages[0].raw_input == "line"


def test_agent_mode_gets_its_own_longer_timeout(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Review B14: the agent loop is several calls long; the 1.5 s translate timeout made it always fall back."""
    from tt_client.config import ClientConfig

    cfg = ClientConfig(gateway_url="http://gw", llm_mode="agent")
    assert cfg.llm_timeout == 1.5 and cfg.llm_agent_timeout >= 15
    from tt_cli.runtime import Runtime

    rt = Runtime.__new__(Runtime)
    rt.cfg = cfg  # type: ignore[attr-defined]
    t = rt.translator()
    assert t.http.timeout.read == cfg.llm_agent_timeout  # type: ignore[attr-defined]
    cfg.llm_mode = "translate"
    assert rt.translator().http.timeout.read == 1.5  # type: ignore[attr-defined]
