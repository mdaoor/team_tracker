"""ContextPack: typed accessors, disk cache with ETag, refresh policy, import/export."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

REFRESH_SECONDS = 15 * 60
DEFAULT_REQUIRED = ["action", "item", "project"]


class ContextPack:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def _names(self, key: str, field: str) -> list[str]:
        return [str(x[field]) for x in self.data.get(key, [])]

    @property
    def project_keys(self) -> list[str]:
        return self._names("projects", "key")

    @property
    def people_handles(self) -> list[str]:
        return self._names("people", "handle")

    @property
    def tags(self) -> list[str]:
        return [str(t) for t in self.data.get("tags", [])]

    @property
    def milestone_names(self) -> list[str]:
        return self._names("milestones", "name")

    @property
    def iteration_names(self) -> list[str]:
        return self._names("iterations", "name")

    @property
    def risk_keys(self) -> list[str]:
        return self._names("risks", "key")

    @property
    def open_items(self) -> list[dict[str, Any]]:
        return list(self.data.get("my_open_items", []))

    @property
    def current_activity(self) -> dict[str, Any] | None:
        """The task the person has running now (key, title), if the server said so."""
        cur = self.data.get("my_current_activity")
        return dict(cur) if cur else None

    # -- Phase 14 additions (manager/role context; absent for plain reporters)
    @property
    def my_role_modes(self) -> list[str]:
        return [str(m) for m in self.data.get("my_role_modes", [])]

    @property
    def my_teams_with_members(self) -> list[dict[str, Any]]:
        return list(self.data.get("my_teams_with_members", []))

    @property
    def team_names(self) -> list[str]:
        return [str(t["name"]) for t in self.my_teams_with_members if t.get("name")]

    @property
    def my_projects_as_manager(self) -> list[str]:
        out: list[str] = []
        for p in self.data.get("my_projects_as_manager", []):
            out.append(str(p["key"] if isinstance(p, dict) else p))
        return out

    @property
    def review_queue_count(self) -> int:
        return int(self.data.get("review_queue_count") or 0)

    @property
    def pending_approvals(self) -> list[dict[str, Any]]:
        return list(self.data.get("pending_approvals", []))

    def item_project(self, *, key: str | None = None, title: str | None = None) -> str | None:
        """The project of one of my open items, found by key or exact title."""
        for i in self.open_items:
            if (key and i["key"] == key) or (title and i["title"].lower() == title.lower()):
                return str(i["project"]) if i.get("project") else None
        return None

    @property
    def required_fields(self) -> list[str]:
        return list(self.data.get("manual_mode", {}).get("required_fields") or DEFAULT_REQUIRED)

    def required_for(self, project_key: str | None) -> list[str]:
        per = self.data.get("manual_mode", {}).get("per_project") or {}
        return list(per.get(project_key or "", None) or self.required_fields)

    def project_by_alias(self, text: str) -> str | None:
        t = text.strip().lower()
        for p in self.data.get("projects", []):
            if t in {p["key"].lower(), p["name"].lower(), *(a.lower() for a in p.get("aliases", []))}:
                return str(p["key"])
        target = {k.lower(): v for k, v in self.data.get("glossary", {}).items()}.get(t)
        if target and str(target).startswith("PROJECT:"):
            return str(target).split(":", 1)[1]
        return None

    def compact(self) -> dict[str, Any]:
        """Only what the translator prompt needs (keeps prompts small)."""
        d = self.data
        return {
            "projects": [
                {"key": p["key"], "name": p["name"], "aliases": p.get("aliases", [])}
                for p in d.get("projects", [])
            ],
            "my_open_items": [{"key": i["key"], "title": i["title"]} for i in d.get("my_open_items", [])],
            "people": [{"handle": p["handle"]} for p in d.get("people", [])],
            "tags": d.get("tags", []),
            "milestones": [{"name": m["name"], "project": m.get("project")} for m in d.get("milestones", [])],
            "iterations": [{"name": i["name"], "project": i.get("project")} for i in d.get("iterations", [])],
            "risks": [{"key": r["key"]} for r in d.get("risks", [])],
            "teams": [
                {
                    "name": t.get("name"),
                    "members": [m if isinstance(m, str) else m.get("handle") for m in t.get("members", [])],
                }
                for t in d.get("my_teams_with_members", [])
            ],
            "my_projects_as_manager": self.my_projects_as_manager,
            "pending_approvals": [
                {"id": a.get("id"), "title": a.get("title")} for a in d.get("pending_approvals", [])
            ],
            "manager_rules": d.get("manager_rules", []),
            "glossary": d.get("glossary", {}),
        }


class PackCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[ContextPack | None, str | None, float]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None, None, 0.0
        return ContextPack(raw["pack"]), raw.get("etag"), float(raw.get("fetched_at", 0))

    def save(self, pack: dict[str, Any], etag: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"etag": etag, "fetched_at": time.time(), "pack": pack}))

    def is_stale(self, now: float | None = None) -> bool:
        _, _, fetched = self.load()
        return (now or time.time()) - fetched > REFRESH_SECONDS

    def refresh(self, api: Any, *, force: bool = False) -> ContextPack | None:
        """Fetch with If-None-Match; on network failure keep serving the cached pack."""
        from tt_client.api import Unreachable

        pack, etag, fetched = self.load()
        if pack is not None and not force and (time.time() - fetched) <= REFRESH_SECONDS:
            return pack
        try:
            data, new_etag = api.context_pack(etag if pack is not None else None)
        except Unreachable:
            return pack
        if data is None and pack is not None:  # 304: unchanged, bump the clock
            self.save(pack.data, etag)
            return pack
        assert data is not None  # noqa: S101
        self.save(data, new_etag)
        return ContextPack(data)

    def import_file(self, path: Path) -> ContextPack:
        data = json.loads(path.read_text())
        if "projects" not in data:
            raise ValueError("not a ContextPack file")
        self.save(data, None)
        return ContextPack(data)

    def export_file(self, path: Path) -> None:
        pack, _, _ = self.load()
        if pack is None:
            raise ValueError("no cached pack to export")
        path.write_text(json.dumps(pack.data, indent=2))
