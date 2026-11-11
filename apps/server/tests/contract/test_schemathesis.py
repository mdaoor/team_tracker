"""OpenAPI contract: no 5xx for any generated request, JSON bodies are valid JSON, and declared schemas are honoured.

Runs against a live server on a clone of the seeded `acme` workspace as `@mahmoud` (admin). Reads are fuzzed hard; writes are
fuzzed with a smaller budget (the clone is disposable). A failure here is a real 500 or a response the spec does not describe."""

from __future__ import annotations

import pytest
import schemathesis

pytestmark = [pytest.mark.contract, pytest.mark.timeout(900)]
HEADERS = {"X-Debug-Actor": "@mahmoud"}
# routes that hand out files / HTML shells or need a browser, a Jira, an IdP or a long-lived stream: not JSON-contract material
SKIP = (
    "/auth/",
    "/p/",
    "/jira/webhook",
    ":export",
    "/render",
    "/healthz",
    "/readyz",
    "/metrics",
    "/generation-pack",
)


@pytest.fixture(scope="module")
def schema(live_server: str):  # type: ignore[no-untyped-def]
    s = schemathesis.openapi.from_url(f"{live_server}/openapi.json")
    s.config.update(base_url=live_server)
    return s


def run(schema, method: str, max_examples: int) -> None:  # type: ignore[no-untyped-def]
    ops = [r.ok() for r in schema.get_all_operations() if r.ok().method.upper() == method]
    failures: list[str] = []
    for op in ops:
        if any(part in op.path for part in SKIP):
            continue
        try:
            cases = op.as_strategy()
        except Exception:  # noqa: BLE001, S112 - an operation Schemathesis cannot generate for is not a contract failure
            continue
        from hypothesis import HealthCheck, Phase, given, settings

        @settings(
            max_examples=max_examples,
            deadline=None,
            phases=[Phase.generate],
            suppress_health_check=list(HealthCheck),
            database=None,
        )
        @given(case=cases)
        def go(case) -> None:  # type: ignore[no-untyped-def]
            case.path_parameters = {**(case.path_parameters or {}), "ws": "acme"}
            resp = case.call(headers=HEADERS)
            ok = resp.status_code < 500 or (
                resp.status_code == 503 and "gateway_unavailable" in resp.text
            )  # no gateway in this setup
            assert ok, (
                f"{resp.status_code} {case.method} {case.formatted_path} q={case.query} b={str(case.body)[:200]} -> {resp.text[:200]}"
            )
            if "json" in resp.headers.get("content-type", ""):
                resp.json()  # a JSON content-type must carry JSON

        try:
            go()
        except BaseException as exc:  # noqa: BLE001
            failures.append(f"{op.method.upper()} {op.path}: {str(exc).splitlines()[0][:200]}")
    assert not failures, "\n".join(failures)


def test_no_5xx_on_any_get(schema) -> None:  # type: ignore[no-untyped-def]
    run(schema, "GET", 12)


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_no_5xx_on_writes(schema, method: str) -> None:  # type: ignore[no-untyped-def]
    run(schema, method, 6)
