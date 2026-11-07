"""The prototype's section arithmetic in Python, for exports that cannot run the deck's JS (PPTX, CSV).

Mirrors `renderKPIs`, `measureOf`/`renderExplorer`, `renderRadar` of docs/spec/team_tracker_presentation.html so a number in the
deck and the same number in a PPTX come from the same formula.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

PRIO_RANK = {"low": 0, "medium": 1, "high": 2, "highest": 3, "critical": 4}


def scoped_violations(snap: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {i["key"] for i in items}
    projects = {i["project"] for i in items}
    return [
        v
        for v in snap["violations"]
        if v["target"] in keys or ("/" in v["target"] and v["project"] in projects)
    ]


def kpis(snap: dict[str, Any], items: list[dict[str, Any]] | None = None) -> list[tuple[str, str, str]]:
    items = snap["items"] if items is None else items
    done = [i for i in items if i["state"] == "done"]
    on_time = [
        i for i in done if not i["actual_end"] or not i["planned_end"] or i["actual_end"] <= i["planned_end"]
    ]
    risk_keys = {r for i in items for r in i["risks"]}
    exposure = sum(r["p"] * r["i"] for r in snap["risks"] if r["key"] in risk_keys)
    wt = sum(i["weight"] for i in done)
    return [
        ("Open", str(sum(1 for i in items if i["state"] == "open")), "not started"),
        ("Active", str(sum(1 for i in items if i["state"] == "active")), "in build / test / staging"),
        ("Done", str(len(done)), "in range"),
        ("Hours", f"{sum(i['hours_logged'] for i in items):.0f}", "logged"),
        ("Weighted throughput", f"{wt:g}", "Σ weight of done"),
        ("On-time", f"{round(len(on_time) / len(done) * 100)}%" if done else "—", "of finished items"),
        ("Violations", str(len(scoped_violations(snap, items))), "open"),
        ("Risk exposure", str(exposure), "Σ p×i of linked risks"),
    ]


def keys_of(i: dict[str, Any], dim: str) -> list[str]:
    if dim == "tags":
        return list(i["tags"]) or ["—"]
    v = i.get(dim)
    return [v if v is not None else "—"]


def measure_of(snap: dict[str, Any], lst: list[dict[str, Any]], measure: str) -> float:
    if measure == "count":
        return len(lst)
    if measure == "hours":
        return float(round(sum(i["hours_logged"] for i in lst)))
    if measure == "wt":
        return float(sum(i["weight"] for i in lst if i["state"] == "done"))
    if measure == "viol":
        return len(scoped_violations(snap, lst))
    return 0


def _sorted_keys(snap: dict[str, Any], vals: set[str], dim: str) -> list[str]:
    if dim == "stage":
        return sorted(vals, key=lambda x: snap["stages"].index(x) if x in snap["stages"] else 99)
    if dim == "priority":
        return sorted(vals, key=lambda x: PRIO_RANK.get(x, 9))
    return sorted(vals)


def explorer(snap: dict[str, Any], dim_a: str, dim_b: str | None, measure: str) -> dict[str, Any]:
    items = snap["items"]
    rows = _sorted_keys(snap, {k for i in items for k in keys_of(i, dim_a)}, dim_a)
    if not dim_b:
        return {
            "rows": rows,
            "cols": None,
            "cells": [
                [measure_of(snap, [i for i in items if r in keys_of(i, dim_a)], measure)] for r in rows
            ],
        }
    cols = _sorted_keys(snap, {k for i in items for k in keys_of(i, dim_b)}, dim_b)
    cells = [
        [
            measure_of(snap, [i for i in items if r in keys_of(i, dim_a) and c in keys_of(i, dim_b)], measure)
            for c in cols
        ]
        for r in rows
    ]
    return {"rows": rows, "cols": cols, "cells": cells}


def radar(snap: dict[str, Any], days: int = 14) -> dict[str, Any]:
    today = date.fromisoformat(snap["as_of"][:10])
    hz = (today + timedelta(days=days)).isoformat()
    t = today.isoformat()
    due = sorted(
        (
            i
            for i in snap["items"]
            if i["state"] != "done" and i["planned_end"] and t <= i["planned_end"] <= hz
        ),
        key=lambda i: i["planned_end"],
    )
    overdue = [i for i in snap["items"] if i["state"] != "done" and i["planned_end"] and i["planned_end"] < t]
    return {"due": due, "overdue": overdue}


def people_rows(snap: dict[str, Any]) -> list[list[str]]:
    items = snap["items"]
    viol = scoped_violations(snap, items)
    out = []
    for p in snap["people"]:
        mine = [i for i in items if i["assignee"] == p["handle"]]
        if not mine:
            continue
        out.append(
            [
                f"{p['name']} {p['handle']}", p["team"] or "—", str(len(mine)), str(sum(1 for i in mine if i["state"] == "active")),
                str(sum(1 for i in mine if i["state"] == "done")), f"{sum(i['hours_logged'] for i in mine):.0f}",
                f"{sum(i['weight'] for i in mine if i['state'] == 'done'):g}", str(sum(1 for v in viol if v["person"] == p["handle"])),
            ]
        )  # fmt: skip
    return out


PEOPLE_HEADER = ["person", "team", "items", "active", "done", "hours", "Σw done", "violations"]
