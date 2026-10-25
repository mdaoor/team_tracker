"""Latency budgets against a running server on a loaded database (phase 12 §1).

    uv run python -m tools.seed.load --url postgresql+psycopg://tt:tt@localhost/tt_load --items 100000 --events 1000000
    TT_DATABASE_URL=... TT_DEV_AUTH=1 uvicorn tt_server.main:create_app --factory --port 8000 &
    uv run python tools/perf_report.py --base http://localhost:8000 --workspace acme > docs/ops/load-report.md

Budgets (plan P12): capture < 150 ms per message (p95), query items < 300 ms (p95), snapshot cold < 2 s."""

from __future__ import annotations

import argparse
import statistics
import time
import uuid

import httpx

BUDGETS = {
    "capture (1 message)": 0.150,
    "query items": 0.300,
    "snapshot (cold)": 2.0,
    "list items": 0.300,
    "review queue": 0.300,
}


def p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[max(0, int(len(xs) * 0.95) - 1)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--workspace", default="acme")
    ap.add_argument("--actor", default="@mahmoud")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--cold", type=int, default=10, help="cold snapshot samples")
    a = ap.parse_args()
    h = {"X-Debug-Actor": a.actor}
    w = f"{a.base}/api/v1/w/{a.workspace}"
    c = httpx.Client(timeout=120, headers=h)
    res: dict[str, list[float]] = {k: [] for k in BUDGETS}

    def timed(name: str, fn) -> None:  # type: ignore[no-untyped-def]
        t0 = time.perf_counter()
        r = fn()
        res[name].append(time.perf_counter() - t0)
        assert r.status_code < 400, (name, r.status_code, r.text[:200])  # noqa: S101

    for i in range(a.n):
        body = {
            "workspace": a.workspace,
            "messages": [
                {
                    "actor": a.actor,
                    "at": "2026-10-23T10:00:00+03:00",
                    "client_id": str(uuid.uuid4()),
                    "action": "note",
                    "note": f"perf {i}",
                }
            ],
        }
        timed("capture (1 message)", lambda body=body: c.post(f"{w}/capture/batches", json=body))
        timed(
            "query items",
            lambda: c.post(
                f"{w}/query/items",
                json={
                    "filter": {"state": {"category": ["open", "active"]}},
                    "sort": [{"field": "priority", "dir": "desc"}],
                    "limit": 50,
                },
            ),
        )
        timed("list items", lambda: c.get(f"{w}/items", params={"limit": 50}))
        timed("review queue", lambda: c.get(f"{w}/review-queue"))
    for i in range(
        3
    ):  # cold: fresh=true bypasses the cache; scope varies so the per-viewer cache cannot help
        scope = ["workspace", "project:WEB", "project:DATA"][i]
        timed(
            "snapshot (cold)",
            lambda scope=scope: c.get(f"{w}/snapshot", params={"scope": scope, "fresh": "true"}),
        )
    print("# Load report\n")  # noqa: T201
    print(
        f"Server `{a.base}`, workspace `{a.workspace}`, {a.n} samples per endpoint ({a.cold} for the cold snapshot).\n"
    )  # noqa: T201
    print("| Endpoint | p50 | p95 | Budget | Result |\n| --- | --- | --- | --- | --- |")  # noqa: T201
    worst = 0
    for name, xs in res.items():
        b = BUDGETS[name]
        ok = p95(xs) <= b
        worst += 0 if ok else 1
        print(
            f"| {name} | {statistics.median(xs) * 1000:.0f} ms | {p95(xs) * 1000:.0f} ms | {b * 1000:.0f} ms | {'meets' if ok else '**misses**'} |"
        )  # noqa: T201
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
