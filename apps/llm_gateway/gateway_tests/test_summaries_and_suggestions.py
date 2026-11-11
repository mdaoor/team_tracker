"""Summaries (grounding, length, alias swap), review/classification suggestions, policy author, rules dry-run, embed."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from tt_llm.config import default_config

from gateway_tests.conftest import PACK, Backend, Harness, request_body, text, tools

FACTS: dict[str, Any] = {
    "project": {"key": "WEB", "name": "Website Refresh"},
    "counts": {"open": 14, "done_this_week": 5, "overdue": 2},
    "items": [
        {"key": "WEB-110", "title": "OAuth v2 token exchange", "state": "doing"},
        {"key": "WEB-111", "title": "Cookie parser", "state": "blocked"},
    ],
    "risks": [
        {"id": "r1", "title": "vendor slips", "score": 15},
        {"id": "r2", "title": "no staging", "score": 6},
    ],
}
GOOD = "Five items closed this week and 14 remain open, two overdue. WEB-111 is blocked. The main risk is that the vendor slips."

# (model body, supporting ids, expected warning fragment or None)
SUMMARIES = [
    (GOOD, ["WEB-110", "WEB-111"], None),
    (GOOD, ["WEB-110"], None),
    (GOOD, [], None),
    (GOOD, ["WEB-110", "WEB-999"], "supporting ids"),  # unknown id dropped
    ("WEB-999 slipped badly.", ["WEB-999"], "absent from the facts"),
    ("x " * 150, ["WEB-110"], "words (limit 120)"),
    ("The risks are vendor slips and no staging.", ["r1", "r2"], "more than one risk"),
    ("Quiet week: 5 items done.", ["WEB-110"], None),
    ("Overdue work: WEB-110 and WEB-111.", ["WEB-110", "WEB-111"], None),
    ("WEB-110 done; WEB-112 unknown.", ["WEB-110"], "absent from the facts"),
]


@pytest.mark.parametrize(("body", "ids", "warn"), SUMMARIES, ids=range(len(SUMMARIES)))
async def test_summary_grounding_rules(
    make: Callable[..., Harness], body: str, ids: list[str], warn: str | None
) -> None:
    h = make(Backend([text({"body": body, "supporting_ids": ids})]))
    r = await h.post("/v1/summarise/project", request_body(facts=FACTS))
    assert r.status_code == 200, r.text
    out = r.json()
    if warn is None:
        assert out["warnings"] == [] and out["output"]["grounded"] is True
    else:
        assert any(warn in w for w in out["warnings"]), out["warnings"]
    assert set(out["output"]["supporting_ids"]) <= {"WEB-110", "WEB-111", "r1", "r2", "WEB"} | {"WEB-110"}


async def test_summary_scopes_and_daily_log(make: Callable[..., Harness]) -> None:
    h = make(
        Backend(
            [text({"body": GOOD, "supporting_ids": []})] * 3
            + [text({"summary": "I finished WEB-110 and logged 3h."})]
        )
    )
    for scope in ("project", "epic", "product"):
        assert (await h.post(f"/v1/summarise/{scope}", request_body(facts=FACTS))).status_code == 200
    assert (await h.post("/v1/summarise/galaxy", request_body(facts=FACTS))).status_code == 502
    r = await h.post(
        "/v1/summarise/daily-log", request_body(facts={"items": [{"key": "WEB-110"}], "minutes": 180})
    )
    assert r.json()["output"] == {"summary": "I finished WEB-110 and logged 3h.", "grounded": True}


async def test_summariser_alias_swap_is_a_config_change(make: Callable[..., Harness]) -> None:
    """Anthropic -> Ollama for `summariser` with no code change: same endpoint, same grounding checks."""
    served: list[str] = []

    def model(kw: dict[str, Any]) -> Any:
        served.append(kw["model"])
        return text({"body": GOOD, "supporting_ids": ["WEB-110"]})

    cloud = make(Backend(default=model), privacy_mode="cloud")
    swapped = make(
        Backend(default=model),
        privacy_mode="local_only",
        aliases={
            **default_config().model_dump()["aliases"],
            "summariser": {"primary": "ollama/qwen2.5:14b-instruct"},
        },
    )
    for h in (cloud, swapped):
        for _ in range(3):
            r = await h.post("/v1/summarise/project", request_body(facts=FACTS))
            assert r.status_code == 200 and r.json()["output"]["grounded"]
    assert (
        served[:3] == ["anthropic/claude-sonnet-5-5"] * 3
        and served[3:] == ["ollama/qwen2.5:14b-instruct"] * 3
    )


REVIEW_ROW = {"kind": "unclassified_project", "phrase": "the billing thing"}


async def test_review_suggestions_are_validated_against_candidates(make: Callable[..., Harness]) -> None:
    ans = {
        "suggestions": [
            {"choice": {"project": "pay"}, "confidence": 0.9, "why": "billing"},
            {"choice": {"project": "NOPE"}, "confidence": 0.99},
            {"choice": {"project": "WEB"}, "confidence": 0.3},
        ]
    }
    h = make(Backend([text(ans)]))
    r = await h.post(
        "/v1/suggest/review", request_body(row=REVIEW_ROW, candidates={"projects": ["PAY", "WEB"]})
    )
    out = r.json()
    assert [s["choice"] for s in out["output"]["suggestions"]] == [{"project": "PAY"}, {"project": "WEB"}]
    assert out["confidence"] == 0.9 and any("NOPE" in w for w in out["warnings"])
    bad = await h.post("/v1/suggest/review", request_body(row={"kind": "risk_mention"}, candidates={}))
    assert bad.status_code == 502


async def test_classification_suggestions_only_use_real_values(make: Callable[..., Harness]) -> None:
    ans = {
        "items": [
            {"key": "WEB-1", "dimension": "region", "value": "emea", "confidence": 0.8},
            {"key": "WEB-1", "dimension": "region", "value": "mars", "confidence": 0.9},
            {"key": "WEB-9", "dimension": "region", "value": "emea", "confidence": 0.9},
            {"key": "WEB-2", "dimension": "region", "value": None, "confidence": 0.1},
        ]
    }
    h = make(Backend([text(ans)]))
    r = await h.post(
        "/v1/suggest/classify",
        request_body(
            items=[{"key": "WEB-1"}, {"key": "WEB-2"}],
            dimensions=[{"key": "region", "values": [{"key": "emea"}, {"key": "apac"}]}],
        ),
    )
    assert r.json()["output"]["items"] == [
        {"key": "WEB-1", "dimension": "region", "value": "emea", "confidence": 0.8}
    ]
    assert len(r.json()["warnings"]) == 2


async def test_policy_author_cannot_claim_validation_that_never_ran(make: Callable[..., Harness]) -> None:
    draft = {
        "mode": "template",
        "template": "stale_item",
        "params": {"days": 7},
        "validated": True,
        "notes": "ok",
    }
    h = make(Backend([tools(("policy_check", {"cel": "item.age_days > 7"})), text("ok"), text(draft)]))
    r = await h.post(
        "/v1/policy/author",
        request_body(request="flag items untouched for a week"),
        **{"X-TT-Core-Token": "t"},
    )
    assert r.status_code == 200
    assert (
        r.json()["output"]["validated"] is False
    )  # the policy engine is not built yet: the tool answered not_available


async def test_rules_dry_run_reports_only_lines_that_change(make: Callable[..., Harness]) -> None:
    def by_rules(kw: dict[str, Any]) -> Any:
        system = kw["messages"][0]["content"]
        prio = (
            "critical"
            if "priority critical" in system and "prod incident" in kw["messages"][1]["content"]
            else None
        )
        return text(
            {
                "messages": [
                    {
                        "action": "start",
                        "item": {"title": "x"},
                        **({"priority": prio, "llm_confidence": {"priority": 0.9}} if prio else {}),
                    }
                ]
            }
        )

    h = make(Backend(default=by_rules))
    r = await h.post(
        "/v1/rules/dry-run",
        request_body(
            lines=["doing fix login", "prod incident: doing restart the api"],
            rules=["prod incident -> priority critical"],
            pack={**PACK, "manager_rules": []},
        ),
    )
    out = r.json()["output"]
    assert out["checked"] == 2 and out["changed"] == 1
    assert out["results"][0]["line"].startswith("prod incident") and "priority" in out["results"][0]["fields"]


async def test_embeddings(make: Callable[..., Harness]) -> None:
    async def fake_embed(**kw: Any) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in kw["texts"]]

    from tt_llm.app import create_app

    h = make()
    h.app = create_app(h.cfg, storage=h.storage, backend=h.backend, embed_backend=fake_embed)
    import httpx

    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app), base_url="http://gw", headers=h.http.headers
    )
    r = await http.post("/v1/embed", json=request_body(texts=["a", "b"]))
    assert r.json()["output"]["dim"] == 3 and len(r.json()["output"]["vectors"]) == 2
    assert (await http.post("/v1/embed", json=request_body(texts=[]))).status_code == 502
    await http.aclose()
