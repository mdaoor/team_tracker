"""A fake of the server's command contract (plan 16 / ADR-0051-0052), for client tests.

It implements exactly what the client may rely on: `POST /commands`, commands inside `POST /capture/batches` (messages first,
then commands, one correlation id), per-command `applied | rejected | previewed | deduplicated`, `GET /commands/catalogue` with
ETag / 304, `GET /me` with `role_modes`, the review queue and team-now reads. It can be used directly as the `api` object of
`tt_client.outbox.sync` (duck-typed) or through `httpx.MockTransport(server.handle)` behind a real `TTApi`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from tt_client.catalogue import Catalogue
from tt_client.commands import PREVIEW_SUFFIX, strip_meta

ServerLog = list[dict[str, Any]]


@dataclass
class FakeServer:
    catalogue_obj: Catalogue
    rejects: dict[str, str] = field(default_factory=dict)  # op -> reason (permission denied etc.)
    fail_messages: set[str] = field(default_factory=set)
    stop_on_failure: bool = False  # a failed message ends processing: later entries get no result at all
    role_modes: list[str] = field(default_factory=lambda: ["reporter", "manager"])
    review_rows: list[dict[str, Any]] = field(default_factory=list)
    team_activities: list[dict[str, Any]] = field(default_factory=list)
    pack: dict[str, Any] | None = None
    workspace: str = "acme"
    etag: str = ""

    def __post_init__(self) -> None:
        self.applied_log: ServerLog = []
        self.requests: list[tuple[str, str]] = []
        self.commands_seen: list[dict[str, Any]] = []  # every command as received (incl. previews)
        self.previews: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {"tags": [], "teams": [], "commands": []}
        self._done: dict[str, dict[str, Any]] = {}
        self.offline = False
        self.etag = self.etag or self.catalogue_obj.etag or '"cat-1"'

    # --------------------------------------------------------------- command semantics
    def _preview(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        keys = (
            args.get("keys")
            or args.get("sources")
            or args.get("rows")
            or ([args["key"]] if "key" in args else [])
        )
        will_create: list[str] = []
        will_delete: list[str] = []
        if op.endswith(".create"):
            will_create.append(
                f"{op.split('.')[0]} {args.get('key') or args.get('name') or args.get('title')}"
            )
        if op in ("team.create", "team.add_members") and args.get("members"):
            will_create.append(f"{len(args['members'])} members")
        if op == "item.merge":
            will_delete = [k for k in args.get("sources", []) if k != args.get("target")]
        if op == "team.remove_members":
            will_delete = list(args.get("members", []))
        return {
            "affected": len(keys),
            "children": 2 if op in ("item.merge", "item.reparent") else 0,
            "overrides_kept": 1 if op == "item.merge" else 0,
            "will_create": will_create,
            "will_delete": will_delete,
            "sample": [{"key": k} for k in list(keys)[:3]],
        }

    def _command(self, cmd: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        self.commands_seen.append(cmd)
        cid, op, args = cmd["client_id"], cmd["op"], dict(cmd.get("args") or {})
        meta = args.get("_meta")
        clean = strip_meta(args)
        if cid in self._done and not cmd.get("preview_only"):
            return {**self._done[cid], "status": "deduplicated"}
        meta_op = self.catalogue_obj.get(op)
        if meta_op is None:
            return self._reject(
                cid, [{"pointer": "/op", "code": "unknown_op", "message": f"unknown op {op}"}]
            )
        issues = self.catalogue_obj.validate(op, clean)
        if issues:
            return self._reject(
                cid, [{"pointer": i.pointer, "code": "invalid_args", "message": i.message} for i in issues]
            )
        if op in self.rejects:
            return self._reject(cid, [{"pointer": "", "code": "forbidden", "message": self.rejects[op]}])
        if cmd.get("preview_only"):
            self.previews.append(
                cmd
            )  # the real server answers a generic preview for ops without a bespoke one
            return {
                "client_id": cid,
                "status": "previewed",
                "result": None,
                "preview": self._preview(op, clean),
                "errors": [],
            }
        self._apply(op, clean)
        self.applied_log.append(
            {
                "kind": "command",
                "id": cid,
                "op": op,
                "correlation_id": correlation_id,
                "meta": meta,
                "on_behalf_of": cmd.get("on_behalf_of"),
            }
        )
        res = {"client_id": cid, "status": "applied", "result": {"op": op}, "preview": None, "errors": []}
        self._done[cid] = res
        return res

    @staticmethod
    def _reject(cid: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
        return {"client_id": cid, "status": "rejected", "result": None, "preview": None, "errors": errors}

    def _apply(self, op: str, args: dict[str, Any]) -> None:
        self.state["commands"].append({"op": op, "args": args})
        if op == "tag.create":
            self.state["tags"].append(args["name"])
        elif op == "item.merge":
            self.state.setdefault("merged", []).append(args)
        elif op == "team.create":
            self.state["teams"].append(args["name"])

    # --------------------------------------------------------------- batch processing
    def process(self, batch: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        correlation_id = uuid.uuid4().hex
        results: list[dict[str, Any]] = []
        stop = False
        for m in batch.get("messages", []):
            if stop:
                break
            if m["client_id"] in self.fail_messages:
                results.append({"client_id": m["client_id"], "status": "failed", "error_code": "boom"})
                stop = self.stop_on_failure
                continue
            self.applied_log.append(
                {"kind": "message", "id": m["client_id"], "correlation_id": correlation_id}
            )
            results.append(
                {
                    "client_id": m["client_id"],
                    "status": "applied",
                    "item": {"key": "WEB-1", "title": m.get("note") or "t"},
                }
            )
        for c in batch.get("commands", []):
            if stop:
                break
            results.append(self._command(c, correlation_id))
        bad = any(r["status"] in ("failed", "rejected") for r in results)
        return (207 if bad else 200), {"results": results, "correlation_id": correlation_id}

    # --------------------------------------------------------------- the `api` duck type used by sync()/services
    def _check_online(self) -> None:
        if self.offline:
            from tt_client.api import Unreachable

            raise Unreachable("fake server offline")

    def post_batch(self, batch: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self._check_online()
        self.requests.append(("POST", "/capture/batches"))
        return self.process(batch)

    def post_commands(self, commands: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        self._check_online()
        self.requests.append(("POST", "/commands"))
        return self.process({"messages": [], "commands": commands})

    def catalogue(self, etag: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        self._check_online()
        self.requests.append(("GET", "/commands/catalogue"))
        if etag == self.etag:
            return None, etag
        return {**self.catalogue_obj.to_doc(), "etag": self.etag}, self.etag

    def me(self) -> dict[str, Any]:
        self._check_online()
        return {"handle": "@mahmoud", "roles": ["manager"], "role_modes": self.role_modes}

    def review_queue(self) -> list[dict[str, Any]]:
        self._check_online()
        return list(self.review_rows)

    def team_now(self) -> list[dict[str, Any]]:
        self._check_online()
        return list(self.team_activities)

    def active_activities(self) -> list[dict[str, Any]]:
        return []

    def daily_logs(self, day: str | None = None) -> list[dict[str, Any]]:
        return []

    def context_pack(self, etag: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        from tt_client.api import Unreachable

        raise Unreachable("no pack in the fake")

    # --------------------------------------------------------------- HTTP face (behind a real TTApi)
    def handle(self, request: httpx.Request) -> httpx.Response:
        prefix = f"/api/v1/w/{self.workspace}"
        path = request.url.path.removeprefix(prefix)
        if self.offline:
            raise httpx.ConnectError("offline")
        if request.method == "GET" and path == "/commands/catalogue":
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304, headers={"ETag": self.etag})
            return httpx.Response(200, json=self.catalogue(None)[0], headers={"ETag": self.etag})
        if request.method == "GET" and path == "/context-pack":
            if self.pack is None:
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            return httpx.Response(200, json=self.pack, headers={"ETag": '"pack-1"'})
        if request.method == "GET" and path == "/me":
            return httpx.Response(200, json=self.me())
        if request.method == "GET" and path == "/review-queue":
            return httpx.Response(200, json={"data": self.review_rows})
        if request.method == "GET" and path == "/activities":
            return httpx.Response(200, json={"data": self.team_activities})
        if request.method == "POST" and path == "/commands":
            body = json.loads(request.content)
            status, out = self.post_commands(body["commands"])
            return httpx.Response(status, json=out)
        if request.method == "POST" and path == "/capture/batches":
            status, out = self.post_batch(json.loads(request.content))
            return httpx.Response(status, json=out)
        return httpx.Response(404, json={"error": {"code": "not_found"}})


__all__ = ["PREVIEW_SUFFIX", "FakeServer", "ServerLog"]
