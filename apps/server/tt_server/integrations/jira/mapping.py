"""Item → Jira issue fields (pure). The desired state is computed from current rows and diffed against the last push by hash."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from tt_server.integrations.jira.client import adf

CONFIG_KEYS = {
    "enabled", "project_key", "issuetype_map", "priority_map", "status_map", "done_status", "stage_field",
    "sprint_field", "auto_send", "sync_time_logs", "sync_comments",
}  # fmt: skip


@dataclass
class Gap:
    """Something we cannot map yet; becomes a `jira_mapping_gap` review row with a one-click fix."""

    field: str  # issuetype_map | priority_map | status_map
    key: str
    options: list[str] = dc_field(default_factory=list)


@dataclass
class Desired:
    fields: dict[str, Any] = dc_field(default_factory=dict)  # sent on create and on update
    create_only: dict[str, Any] = dc_field(default_factory=dict)  # issuetype, project
    state_key: str | None = None
    target_status: str | None = None
    gaps: list[Gap] = dc_field(default_factory=list)

    @property
    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.fields, sort_keys=True, default=str).encode()).hexdigest()


def validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Shape-check the per-project mapping; returns the cleaned dict or raises ValueError naming the field."""
    bad = set(cfg) - CONFIG_KEYS
    if bad:
        raise ValueError(f"unknown keys {sorted(bad)}")
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k.endswith("_map"):
            if not isinstance(v, dict) or not all(
                isinstance(a, str) and isinstance(b, str) for a, b in v.items()
            ):
                raise ValueError(f"{k} must map names to names")
        elif k in ("enabled", "auto_send", "sync_time_logs", "sync_comments"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif v is not None and not isinstance(v, str):
            raise ValueError(f"{k} must be text")
        out[k] = v
    if out.get("enabled") and not out.get("project_key"):
        raise ValueError("project_key is required when the link is enabled")
    return out


def desired(
    item: dict[str, Any], cfg: dict[str, Any], *, assignee_account: str | None, stage_key: str | None
) -> Desired:
    """`item`: title, description, type, priority, planned_end, estimate_hours, tags, state_key, id."""
    d = Desired()
    f = d.fields
    f["summary"] = item["title"][:255]
    f["description"] = adf(item.get("description") or "")
    labels = sorted({f"tt:{item['id']}", *(t.replace(" ", "_") for t in item.get("tags", []))})
    f["labels"] = labels
    f["duedate"] = item["planned_end"].isoformat() if item.get("planned_end") else None
    if assignee_account is not None or item.get("assignee_unassigned"):
        f["assignee"] = {"accountId": assignee_account} if assignee_account else None
    if item.get("estimate_hours") is not None:
        f["timetracking"] = {"originalEstimate": f"{_num(item['estimate_hours'])}h"}
    prio = (cfg.get("priority_map") or {}).get(item["priority"])
    if prio:
        f["priority"] = {"name": prio}
    else:
        d.gaps.append(Gap("priority_map", item["priority"]))
    if cfg.get("stage_field") and stage_key:
        f[cfg["stage_field"]] = stage_key
    itype = (cfg.get("issuetype_map") or {}).get(item["type"])
    if itype:
        d.create_only["issuetype"] = {"name": itype}
    else:
        d.gaps.append(Gap("issuetype_map", item["type"]))
    d.create_only["project"] = {"key": cfg.get("project_key")}
    d.state_key = item["state_key"]
    d.target_status = (cfg.get("status_map") or {}).get(item["state_key"])
    if d.target_status is None:
        d.gaps.append(Gap("status_map", item["state_key"]))
    return d


def diff(old_hash: str | None, new: Desired) -> bool:
    return old_hash != new.hash


def _num(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


def reverse_status(cfg: dict[str, Any], jira_status: str) -> str | None:
    """Jira status name → workflow state key (first match; the done status prefers the mapped done state)."""
    sm: dict[str, str] = cfg.get("status_map") or {}
    hits = [k for k, v in sm.items() if v.lower() == jira_status.lower()]
    return hits[0] if hits else None
