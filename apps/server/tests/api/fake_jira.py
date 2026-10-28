"""An in-memory Jira Cloud for contract-style tests: just enough of REST v3 for the sync (issues, transitions, worklogs, search)."""

from __future__ import annotations

import json
from typing import Any

import httpx


class FakeJira:
    def __init__(self) -> None:
        self.issues: dict[str, dict[str, Any]] = {}
        self.worklogs: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.link_types = {"Duplicate", "Relates", "Blocks"}
        self.calls: list[tuple[str, str]] = []
        self.users = {"sara@acme.test": "acc-sara", "mahmoud@acme.test": "acc-mahmoud"}
        self.down = False
        self.fail_with: list[int] = []  # statuses to answer with, in order, before behaving normally
        self.retry_after = "0"
        self.lose_create_response = False
        self.statuses = ["To Do", "In Progress", "Done"]
        self.blocked_statuses: set[str] = set()
        self.n = 100

    # ---------------------------------------------------------------- helpers
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if self.down:
            return httpx.Response(503, json={"errorMessages": ["down"]})
        if self.fail_with:
            code = self.fail_with.pop(0)
            return httpx.Response(
                code, headers={"Retry-After": self.retry_after}, json={"errorMessages": ["busy"]}
            )
        path, q = req.url.path, dict(req.url.params)
        body = json.loads(req.content) if req.content else None
        m = req.method
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"accountId": "bot-acc", "displayName": "TT bot"})
        if path == "/rest/api/3/user/search":
            acc = self.users.get(q.get("query", "").lower())
            return httpx.Response(200, json=[{"accountId": acc, "emailAddress": q["query"]}] if acc else [])
        if path == "/rest/api/3/search/jql":
            jql = q.get("jql", "")
            hits = [
                {
                    "id": i["id"],
                    "key": k,
                    "fields": {
                        "summary": i["fields"].get("summary"),
                        "labels": i["fields"].get("labels", []),
                        "status": {"name": i["status"]},
                    },
                }
                for k, i in self.issues.items()
                if (
                    'labels = "' not in jql
                    or jql.split('labels = "')[1].split('"')[0] in i["fields"].get("labels", [])
                )
            ]
            return httpx.Response(200, json={"issues": hits})
        if path == "/rest/api/3/issue" and m == "POST":
            self.n += 1
            key = f"WEB-{self.n}"
            self.issues[key] = {"id": str(self.n), "fields": dict(body["fields"]), "status": "To Do"}
            if self.lose_create_response:
                self.lose_create_response = False
                return httpx.Response(504, json={})  # created, but the caller never learns
            return httpx.Response(201, json={"id": str(self.n), "key": key})
        if path == "/rest/api/3/issueLink" and m == "POST":
            if body["type"]["name"] not in self.link_types:
                return httpx.Response(404, json={"errorMessages": ["No issue link type with name found"]})
            self.links.append(
                {
                    "type": body["type"]["name"],
                    "inward": body["inwardIssue"]["key"],
                    "outward": body["outwardIssue"]["key"],
                }
            )
            return httpx.Response(201)
        parts = path.split("/")
        if len(parts) >= 6 and parts[4] == "issue":
            key = parts[5]
            if key not in self.issues:
                return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
            issue = self.issues[key]
            tail = parts[6] if len(parts) > 6 else ""
            if tail == "" and m == "PUT":
                issue["fields"].update(body["fields"])
                return httpx.Response(204)
            if tail == "" and m == "GET":
                return httpx.Response(
                    200,
                    json={
                        "id": issue["id"],
                        "key": key,
                        "fields": {**issue["fields"], "status": {"name": issue["status"]}},
                    },
                )
            if tail == "transitions" and m == "GET":
                return httpx.Response(
                    200,
                    json={
                        "transitions": [
                            {"id": str(i), "name": s, "to": {"name": s}}
                            for i, s in enumerate(self.statuses)
                            if s != issue["status"] and s not in self.blocked_statuses
                        ]
                    },
                )
            if tail == "transitions" and m == "POST":
                issue["status"] = self.statuses[int(body["transition"]["id"])]
                return httpx.Response(204)
            if tail == "worklog" and m == "GET":
                return httpx.Response(
                    200,
                    json={
                        "worklogs": [
                            {"id": f"wl-{i + 1}", **w}
                            for i, w in enumerate(self.worklogs)
                            if w["issue"] == key
                        ]
                    },
                )
            if tail == "worklog":
                self.worklogs.append({"issue": key, **body})
                return httpx.Response(201, json={"id": f"wl-{len(self.worklogs)}"})
            if tail == "comment":
                self.comments.append({"issue": key, **body})
                return httpx.Response(201, json={"id": f"c-{len(self.comments)}"})
        if "/project/" in path and path.endswith("/statuses"):
            return httpx.Response(
                200,
                json=[
                    {"name": "Task", "statuses": [{"name": s} for s in self.statuses]},
                    {"name": "Bug", "statuses": []},
                ],
            )
        if path == "/rest/api/3/priority":
            return httpx.Response(200, json=[{"name": "High"}, {"name": "Medium"}])
        if path == "/rest/api/3/field":
            return httpx.Response(
                200, json=[{"id": "customfield_10001", "name": "Delivery Stage", "custom": True}]
            )
        return httpx.Response(404, json={"errorMessages": [f"unmocked {m} {path}"]})
