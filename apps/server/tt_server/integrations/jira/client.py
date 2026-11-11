"""Jira Cloud REST v3 client: retry/backoff, `Retry-After` on 429, bounded timeouts. Only the calls the sync needs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class JiraError(Exception):
    """A request Jira refused (4xx other than 429): retrying will not help."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Jira {status}: {message}")
        self.status = status
        self.message = message


class JiraUnavailable(Exception):
    """Network failure, 5xx or exhausted 429 retries: the push is queued and retried later."""


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retries: int = 3,
        backoff_s: float = 0.5,
        timeout_s: float = 10.0,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._c = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=(email, token),
            headers={"Accept": "application/json"},
            transport=transport,
            timeout=timeout_s,
        )
        self._retries, self._backoff, self._sleep = retries, backoff_s, sleep

    async def aclose(self) -> None:
        await self._c.aclose()

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        retry_5xx: bool = True,
    ) -> Any:
        """`retry_5xx=False` for writes that are not idempotent: a 5xx or timeout may mean "done, but we never heard", so the
        caller (the job, with its label / marker lookups) decides what to do. A 429 was never processed and is always retried."""
        last = "no response"
        for attempt in range(self._retries + 1):
            try:
                r = await self._c.request(method, path, json=json, params=params)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                if not retry_5xx:
                    raise JiraUnavailable(last) from exc
            else:
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    if r.status_code >= 500 and not retry_5xx:
                        raise JiraUnavailable(last)
                    if attempt < self._retries:
                        wait = float(r.headers.get("Retry-After") or self._backoff * 2**attempt)
                        await self._sleep(min(wait, 30.0))
                        continue
                    raise JiraUnavailable(last)
                if r.status_code >= 400:
                    raise JiraError(r.status_code, _message(r))
                return r.json() if r.content else None
            if attempt < self._retries:
                await self._sleep(self._backoff * 2**attempt)
        raise JiraUnavailable(last)

    # ---- reads
    async def myself(self) -> dict[str, Any]:
        return await self._req("GET", "/rest/api/3/myself")  # type: ignore[no-any-return]

    async def get_issue(self, key: str) -> dict[str, Any]:
        out: dict[str, Any] = await self._req(
            "GET", f"/rest/api/3/issue/{key}", params={"fields": "status,assignee,summary,labels"}
        )
        return out

    async def search(self, jql: str, fields: str = "summary,labels,status") -> list[dict[str, Any]]:
        out = await self._req(
            "GET", "/rest/api/3/search/jql", params={"jql": jql, "fields": fields, "maxResults": 100}
        )
        return list((out or {}).get("issues", []))

    async def transitions(self, key: str) -> list[dict[str, Any]]:
        out = await self._req("GET", f"/rest/api/3/issue/{key}/transitions")
        return list((out or {}).get("transitions", []))

    async def find_user(self, email: str) -> str | None:
        users = await self._req("GET", "/rest/api/3/user/search", params={"query": email})
        for u in users or []:
            if str(u.get("emailAddress", "")).lower() == email.lower():
                return str(u["accountId"])
        return str(users[0]["accountId"]) if users else None

    async def project_options(self, project_key: str) -> dict[str, Any]:
        statuses = await self._req("GET", f"/rest/api/3/project/{project_key}/statuses")
        priorities = await self._req("GET", "/rest/api/3/priority")
        fields = await self._req("GET", "/rest/api/3/field")
        return {
            "issuetypes": sorted({t["name"] for t in statuses or []}),
            "statuses": sorted({s["name"] for t in statuses or [] for s in t.get("statuses", [])}),
            "priorities": [p["name"] for p in priorities or []],
            "fields": [{"id": f["id"], "name": f["name"]} for f in fields or [] if f.get("custom")],
        }

    # ---- writes
    async def create_issue(self, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/rest/api/3/issue", json={"fields": fields}, retry_5xx=False)  # type: ignore[no-any-return]

    async def update_issue(self, key: str, fields: dict[str, Any]) -> None:
        await self._req("PUT", f"/rest/api/3/issue/{key}", json={"fields": fields})

    async def transition(self, key: str, transition_id: str) -> None:
        await self._req(
            "POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": transition_id}}
        )

    async def add_comment(self, key: str, adf: dict[str, Any]) -> None:
        await self._req("POST", f"/rest/api/3/issue/{key}/comment", json={"body": adf})

    async def link_issues(self, inward_key: str, outward_key: str, link_type: str = "Duplicate") -> None:
        """Link two issues: `outward_key` <outward description> `inward_key` (for "Duplicate": outward duplicates inward)."""
        await self._req(
            "POST",
            "/rest/api/3/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_key},
                "outwardIssue": {"key": outward_key},
            },
        )

    async def list_worklogs(self, key: str) -> list[dict[str, Any]]:
        out = await self._req("GET", f"/rest/api/3/issue/{key}/worklog")
        return list((out or {}).get("worklogs", []))

    async def add_worklog(self, key: str, seconds: int, started: str, comment: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"timeSpentSeconds": seconds, "started": started}
        if comment:
            body["comment"] = adf(comment)
        return await self._req("POST", f"/rest/api/3/issue/{key}/worklog", json=body, retry_5xx=False)  # type: ignore[no-any-return]


def adf(text: str) -> dict[str, Any]:
    """Plain text → Atlassian Document Format: one paragraph per blank-line-separated block."""
    blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()] or [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": b}] if b else []} for b in blocks
        ],
    }


def adf_text(node: Any) -> str:
    """Flatten ADF (or a plain string) to text for inbound comments."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    parts = [adf_text(c) for c in node.get("content", [])]
    return "\n\n".join(parts) if node.get("type") == "doc" else "".join(parts)


def _message(r: httpx.Response) -> str:
    try:
        body = r.json()
        msgs = [*body.get("errorMessages", []), *body.get("errors", {}).values()]
        return "; ".join(str(m) for m in msgs) or r.text[:200]
    except Exception:
        return r.text[:200]
