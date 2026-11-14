"""Smoke test of a running install: health, demo data, web console, and a deck exported to PDF through Chromium.

Standard library only, so it runs with the bundle's Python on a host without curl:
    python ci/ops/smoke.py [--api URL] [--web URL] [--workspace acme]
Needs TT_DEV_AUTH=1 on the server (it acts as the demo admin @mahmoud). Exit 0 when everything passes.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--api", default="http://127.0.0.1:8000")
p.add_argument("--web", default="http://127.0.0.1:5173")
p.add_argument("--workspace", default="acme")
a = p.parse_args()
H = {"X-Debug-Actor": "@mahmoud", "content-type": "application/json"}


def req(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None, timeout: int = 120) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:  # noqa: S310 - local URLs given by the caller
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{name:<34} {'ok' if ok else 'FAILED'}{(' (' + detail + ')') if detail else ''}", flush=True)
    if not ok:
        sys.exit(1)


api, ws = a.api.rstrip("/"), a.workspace
s, b = req(f"{api}/healthz"); check("GET /healthz", s == 200)
s, b = req(f"{api}/readyz"); check("GET /readyz (db + job queue)", s == 200 and b'"ready"' in b, b.decode()[:80])
s, b = req(f"{api}/api/v1/w/{ws}/items?limit=1", headers=H); check("demo workspace has work items", s == 200 and b'"id"' in b)
s, b = req(f"{a.web}/login"); check("web console served", s == 200 and b"<html" in b.lower())
s, b = req(f"{a.web}/readyz"); check("web console proxies the API", s == 200)
s, b = req(f"{api}/api/v1/w/{ws}/presentations:from-template", "POST", {"template": "exec_summary", "title": "smoke"}, H)
pid = json.loads(b).get("id") if s in (200, 201) else None
check("deck created", bool(pid), str(pid or b[:200]))
s, b = req(f"{api}/api/v1/w/{ws}/presentations/{pid}/render?format=pdf", "POST", None, H, timeout=180)
check("deck exported to PDF", s == 200 and b[:4] == b"%PDF", f"{len(b)} bytes" if s == 200 else f"HTTP {s}: {b[:300]!r}")
print("smoke test passed")
