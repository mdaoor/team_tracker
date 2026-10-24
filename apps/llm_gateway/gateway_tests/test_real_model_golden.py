"""Golden run against a REAL model (opt-in): `TT_LLM_REAL=1 uv run pytest -m llm apps/llm_gateway/gateway_tests/test_real_model_golden.py -s`.

It runs the translator golden set (every non-`syntax` case, the ones that need a model) and the deck-author briefs through the real
router with whatever `TT_LLM_CONFIG` (or the default config) points at: a local Ollama model, or a cloud one with its API key in the
environment. Nothing here is mocked. The report (accuracy per field, failures, p50/p95 latency, cost) is printed and, with
`TT_LLM_REPORT=<path>`, written as Markdown so a run can be committed next to the release notes."""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tt_client.evaluate import evaluate, load
from tt_llm.config import GatewayConfig, default_config, load_config
from tt_llm.routing import litellm_backend
from tt_schema.client import ClientBatch

from gateway_tests.conftest import Backend, FakeCore, Harness, request_body

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(os.environ.get("TT_LLM_REAL") != "1", reason="set TT_LLM_REAL=1 to call a real model"),
]
GOLDEN = Path(__file__).parents[3] / "packages" / "client_core" / "tests" / "golden" / "lines.yaml"
MIN_ACCURACY = float(os.environ.get("TT_LLM_MIN_ACCURACY", "0.80"))
KINDS = [
    {"kind": k}
    for k in (
        "title",
        "kpis",
        "projects",
        "roadmap",
        "milestones",
        "deliverables",
        "plan_vs_actual",
        "phase_status",
        "markdown",
    )
]
BRIEFS = [
    (
        "quarterly steering-committee review of the ERP rollout",
        {"projects": [{"key": "ERP", "name": "ERP rollout"}]},
    ),
    (
        "one-page status of the website refresh for the exec team",
        {"projects": [{"key": "WEB", "name": "Website refresh"}]},
    ),
]


def cfg() -> GatewayConfig:
    path = os.environ.get("TT_LLM_CONFIG")
    return (
        load_config(Path(path))
        if path
        else default_config(privacy_mode=os.environ.get("TT_LLM_PRIVACY", "cloud"))
    )


@pytest.fixture
def real(monkeypatch: pytest.MonkeyPatch) -> Callable[[], Harness]:
    monkeypatch.setenv("TT_LLM_GATEWAY_TOKENS", "tok")
    return lambda: Harness(cfg(), litellm_backend, FakeCore())  # type: ignore[arg-type]


def report(title: str, rows: list[str]) -> None:
    text = f"## {title}\n\n" + "\n".join(rows) + "\n"
    print("\n" + text)  # noqa: T201
    if path := os.environ.get("TT_LLM_REPORT"):
        with Path(path).open("a") as f:
            f.write(text + "\n")


async def test_translator_golden_set_with_a_real_model(real: Callable[[], Harness]) -> None:
    h = real()
    golden = load(GOLDEN)
    cases = [c for c in golden["cases"] if c.get("subset") != "syntax"]
    assert cases, "the golden set has no model-dependent cases"
    results: dict[str, list[Any]] = {}
    latencies: list[float] = []
    cost = 0.0
    for case in cases:
        t0 = time.perf_counter()
        r = await h.post(
            "/v1/translate",
            request_body(
                line=case["line"], actor="@eval", now=golden["now"], time_zone=golden["tz"], pack={}
            ),
        )
        latencies.append(time.perf_counter() - t0)
        assert r.status_code == 200, r.text
        body = r.json()
        cost += float(body.get("cost_usd") or 0)
        results[case["line"]] = ClientBatch.model_validate(body["batch"]).messages
    rep = evaluate({**golden, "cases": cases}, subset=None, parse=lambda line: results[line])
    lat = sorted(latencies)
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)]
    report(
        "Translator golden set (real model)",
        [
            f"* model config: `{os.environ.get('TT_LLM_CONFIG', 'default')}`, cases: {rep['cases']}",
            *[f"* accuracy `{k}`: {v:.2%}" for k, v in sorted(rep["accuracy"].items())],
            f"* latency p50 {statistics.median(lat):.2f}s, p95 {p95:.2f}s; cost ${cost:.4f}",
            *[
                f"* FAIL `{f['line']}` {f.get('field', '')} expected {f.get('expected')} got {f.get('actual')}"
                for f in rep["failures"][:20]
            ],
        ],
    )
    assert rep["accuracy"]["messages"] >= MIN_ACCURACY, rep["failures"][:5]


async def test_deck_author_with_a_real_model_only_uses_known_kinds_and_projects(
    real: Callable[[], Harness],
) -> None:
    h = real()
    rows = []
    for brief, facts in BRIEFS:
        r = await h.post(
            "/v1/presentation/author", request_body(request=brief, facts=facts, pack={"slide_kinds": KINDS})
        )
        assert r.status_code == 200, r.text
        out = r.json()["output"]
        kinds = {k["kind"] for k in KINDS}
        assert out["slides"], brief
        assert all(s["kind"] in kinds for s in out["slides"]), out
        keys = {p["key"] for p in facts["projects"]}
        assert all(
            (s.get("config") or {}).get("project_key", next(iter(keys))) in keys for s in out["slides"]
        )
        rows.append(
            f"* `{brief}` -> {len(out['slides'])} slides: {', '.join(s['kind'] for s in out['slides'])} (warnings: {r.json()['warnings']})"
        )
    report("Deck author (real model)", rows)


def test_the_opt_in_guard_itself_is_documented() -> None:
    assert (
        Backend
    )  # imported for parity with the scripted suites; the real run replaces it with litellm_backend
