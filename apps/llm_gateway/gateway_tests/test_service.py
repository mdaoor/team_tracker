"""Run traces (memory + Postgres schema), admin endpoint, config loading, CLI wiring."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from tt_llm.config import load_config
from tt_llm.storage import Run, SqlStorage, Step

from gateway_tests.conftest import CORE_TOKEN, Backend, Harness, request_body, text, tools
from gateway_tests.test_capture_agent import final


async def test_runs_endpoint_is_admin_only_and_returns_the_replayable_trace(
    make: Callable[..., Harness],
) -> None:
    h = make(Backend([tools(("list_people", {})), text("ok"), final()]))
    r = await h.post(
        "/v1/agent/capture", request_body(line="finished WEB-110"), **{"X-TT-Core-Token": CORE_TOKEN}
    )
    rid = r.json()["run_id"]
    assert (await h.http.get(f"/v1/runs/{rid}")).status_code == 403  # a normal gateway token is not enough
    got = await h.http.get(f"/v1/runs/{rid}", headers={"Authorization": "Bearer admin-secret"})
    body = got.json()
    assert got.status_code == 200 and body["task"] == "agent_capture" and body["status"] == "ok"
    assert [s["kind"] for s in body["steps"]] == ["model", "tool", "model", "model", "model"][
        : len(body["steps"])
    ]
    assert body["steps"][1]["name"] == "list_people"
    assert (
        await h.http.get("/v1/runs/nope", headers={"Authorization": "Bearer admin-secret"})
    ).status_code == 404


async def test_raw_reporter_text_is_not_stored_by_default(make: Callable[..., Harness]) -> None:
    h = make(Backend([text({"messages": [{"action": "note", "note": "n"}]})]))
    r = await h.post("/v1/translate", request_body(line="note: my secret plan"))
    run = await h.storage.get_run(r.json()["run_id"])
    assert run is not None and "secret" not in str(run.meta)
    h2 = make(Backend([text({"messages": [{"action": "note", "note": "n"}]})]), log_raw_input=True)
    r2 = await h2.post("/v1/translate", request_body(line="note: my secret plan"))
    run2 = await h2.storage.get_run(r2.json()["run_id"])
    assert run2 is not None and run2.meta["raw_input"] == "note: my secret plan"


async def test_sql_storage_round_trips_runs_and_budgets() -> None:
    url = os.environ.get("TT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs TT_TEST_DATABASE_URL")
    from datetime import UTC, datetime

    store = SqlStorage.from_url(url, "llm_gateway_test")
    await store.create()
    try:
        run = Run(
            "run-1",
            "agent_capture",
            "acme",
            "@mahmoud",
            "agent",
            status="ok",
            model="ollama/x",
            cost_usd=0.0123,
        )
        run.ended_at = datetime.now(UTC)
        run.steps = [
            Step(1, "model", "ollama/x", {"m": 1}, {"tool_calls": ["a"]}, 12),
            Step(2, "tool", "a", {}, {"ok": True}, 3),
        ]
        run.meta = {"rationale": "because"}
        await store.save_run(run)
        run.status = "error"
        await store.save_run(run)  # upsert
        got = await store.get_run("run-1")
        assert got is not None and got.status == "error" and got.meta == {"rationale": "because"}
        assert [(s.n, s.name) for s in got.steps] == [(1, "ollama/x"), (2, "a")] and round(
            got.cost_usd, 4
        ) == 0.0123
        assert await store.get_run("missing") is None
        day = datetime.now(UTC).date()
        await store.add_spend("acme", day, 0.5)
        await store.add_spend("acme", day, 0.25)
        assert await store.spent("acme", day) == 0.75 and await store.spent("other", day) == 0.0
    finally:
        import sqlalchemy as sa

        async with store.engine.begin() as conn:
            await conn.execute(sa.text('DROP SCHEMA "llm_gateway_test" CASCADE'))
        await store.engine.dispose()


def test_config_file_and_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "llm_gateway.yaml"
    f.write_text(
        "privacy_mode: hybrid\n"
        "aliases:\n  translator: {primary: ollama/tiny, fallbacks: []}\n"
        "limits: {max_steps: 3}\n"
        "budgets: {default_usd_per_workspace_per_day: 1, workspaces: {acme: 9}}\n"
        "server: {base_url: https://tracker.corp.example}\n"
    )
    cfg = load_config(f)
    assert (
        cfg.aliases["translator"].primary == "ollama/tiny"
        and cfg.limits.max_steps == 3
        and cfg.budgets.workspaces["acme"] == 9
    )
    monkeypatch.setenv("TT_LLM_PRIVACY_MODE", "local_only")
    monkeypatch.setenv("TT_LLM_SERVER_URL", "http://localhost:8000")
    cfg2 = load_config(f)
    assert cfg2.privacy_mode == "local_only" and cfg2.server.base_url == "http://localhost:8000"
    assert load_config(tmp_path / "missing.yaml").aliases["agent"].primary.startswith("ollama/")


def test_local_only_flag_forces_the_privacy_mode_and_anonymous_loopback() -> None:
    from tt_llm.config import default_config
    from tt_llm.main import apply_local_only

    cfg = apply_local_only(default_config(privacy_mode="cloud"), "127.0.0.1")
    assert cfg.privacy_mode == "local_only" and cfg.auth.allow_anonymous is True
    exposed = apply_local_only(default_config(privacy_mode="cloud"), "0.0.0.0")  # noqa: S104
    assert exposed.privacy_mode == "local_only" and exposed.auth.allow_anonymous is False
    with pytest.raises(ValueError, match="localhost"):
        apply_local_only(default_config(ollama_api_base="http://gpu.corp:11434"), "127.0.0.1")


async def test_a_workspace_token_cannot_charge_another_workspace(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B6: the budget key is the workspace the token is bound to, not a string the caller writes."""
    from gateway_tests.conftest import request_body

    monkeypatch.setenv("TT_LLM_WORKSPACE_TOKENS", "ws-acme-secret=acme")
    h = make()
    ok = await h.http.post(
        "/v1/embed",
        json={**request_body(), "texts": ["x"]},
        headers={"Authorization": "Bearer ws-acme-secret"},
    )
    assert ok.status_code != 403 and ok.status_code != 401
    bad = await h.http.post(
        "/v1/embed",
        json={**request_body(workspace="victim"), "texts": ["x"]},
        headers={"Authorization": "Bearer ws-acme-secret"},
    )
    assert bad.status_code == 403
    missing = await h.http.post(
        "/v1/embed",
        json={**request_body(workspace=""), "texts": ["x"]},
        headers={"Authorization": "Bearer ws-acme-secret"},
    )
    assert missing.status_code == 403
    nope = await h.http.post("/v1/embed", json={**request_body()}, headers={"Authorization": "Bearer other"})
    assert nope.status_code == 401
    await h.http.aclose()
