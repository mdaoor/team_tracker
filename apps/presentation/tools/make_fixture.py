"""Wrap tools/gen_snapshot.py's output into the data endpoint's shape: `fixtures/golden.json` (deterministic, seed 7)."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "golden.json"

with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(  # noqa: S603 - our own generator
        [sys.executable, str(ROOT / "tools" / "gen_snapshot.py")], cwd=tmp, check=True, capture_output=True
    )
    snap = json.loads((Path(tmp) / "snapshot.json").read_text())

snap["schema_version"] = 1
snap["meta"] = {"viewer": "@mahmoud", "scope_label": "Workspace: acme", "generated_for": "@mahmoud"}
snap.setdefault("processes", None)
if snap["processes"] is None:
    del snap["processes"]
slides = [
    ("kpis", "Overview", {}), ("projects", "Projects & epics", {}), ("pipeline", "Delivery pipeline", {}),
    ("explorer", "Dimension explorer", {"dimA": "team", "dimB": "stage", "measure": "count"}), ("radar", "Delivery radar", {"days": 14}),
    ("gantt", "Gantt", {"rows": "epic"}), ("now", "Now", {}), ("people", "Team activity", {}), ("violations", "Violations & habits", {"with_habits": True}),
]  # fmt: skip
pres = {
    "id": "golden", "title": "Portfolio review", "scope": "workspace", "range_days": 14, "theme": "light",
    "slides": [{"id": f"s{n}", "kind": k, "title": t, "subtitle": None, "config": c, "notes": f"Notes for {t}", "layout": "full", "live": False, "position": n} for n, (k, t, c) in enumerate(slides)],
}  # fmt: skip
OUT.write_text(json.dumps({"presentation": pres, "snapshot": snap, "slide_data": {}}, indent=1))
print(OUT, len(snap["items"]), "items")
