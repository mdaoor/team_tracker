from __future__ import annotations

import httpx
from jsonschema import Draft202012Validator
from tt_schema import client_message_json_schema


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["X-Request-Id"]


async def test_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-Id": "abc-123"})
    assert r.headers["X-Request-Id"] == "abc-123"


async def test_readyz_checks_database_and_procrastinate(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "procrastinate": "ok"}}


async def test_client_message_schema_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/meta/schema/client-message")
    assert r.status_code == 200 and r.json() == client_message_json_schema()
    Draft202012Validator.check_schema(r.json())
    batch = await client.get("/api/v1/meta/schema/client-message", params={"batch": "true"})
    assert "messages" in batch.json()["properties"]


async def test_error_envelope_for_unknown_route(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/nope")
    assert r.status_code == 404
    assert set(r.json()["error"]) == {"code", "message", "pointer", "details"}
    assert r.json()["error"]["code"] == "not_found"


async def test_error_envelope_for_validation(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/meta/schema/client-message", params={"batch": "maybe"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["pointer"] == "/batch"
