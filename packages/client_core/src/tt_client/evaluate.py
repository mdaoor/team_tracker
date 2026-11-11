"""Golden-set evaluation: `tt-eval translator --golden lines.yaml` prints per-field accuracy."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from tt_schema.client import ClientMessage

from tt_client.parser.structured import ParseContext, StructuredParser

FIELDS = [
    "action",
    "title",
    "key",
    "minutes",
    "planned_end",
    "estimate",
    "tags",
    "project",
    "priority",
    "note",
    "on_behalf_of",
    "item_type",
    "assignees",
    "evidence",
]


def _actual(msg: ClientMessage, field: str) -> Any:
    if field == "title":
        return msg.item.title if msg.item else None
    if field == "key":
        return msg.item.key if msg.item else None
    if field == "action":
        return msg.action.value
    if field == "planned_end":
        return msg.planned_end.isoformat() if msg.planned_end else None
    if field == "priority":
        return msg.priority.value if msg.priority else None
    if field == "item_type":
        return msg.item_type.value if msg.item_type else None
    if field == "assignees":
        return [a["person"] for a in msg.assignees]
    if field == "evidence":
        return [{"kind": e["kind"]} for e in msg.evidence or []]
    return getattr(msg, field)


def _matches(actual: Any, expected: Any, field: str) -> bool:
    if field == "title" and isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    if field == "evidence":
        return [{"kind": e["kind"]} for e in actual] == expected if actual else not expected
    return bool(actual == expected)


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())  # type: ignore[no-any-return]


def evaluate(golden: dict[str, Any], *, subset: str | None = None, parse: Any = None) -> dict[str, Any]:
    """Run `parse(line) -> list[ClientMessage]` (default: structured parser) over the golden cases."""
    now = datetime.fromisoformat(golden["now"])
    ctx = ParseContext(actor="@eval", now=now, tz=golden.get("tz", "UTC"))
    parser = StructuredParser(ctx)
    parse = parse or (lambda line: parser.parse(line).messages)
    hits: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    failures: list[dict[str, Any]] = []
    cases = [c for c in golden["cases"] if subset is None or c.get("subset") == subset]
    for case in cases:
        msgs = parse(case["line"])
        expected = case["expect"]
        ok = len(msgs) == len(expected)
        total["messages"] += 1
        hits["messages"] += ok
        if not ok:
            failures.append(
                {"line": case["line"], "problem": f"{len(msgs)} messages, expected {len(expected)}"}
            )
            continue
        for msg, exp in zip(msgs, expected, strict=True):
            for f, want in exp.items():
                total[f] += 1
                got = _actual(msg, f)
                if _matches(got, want, f):
                    hits[f] += 1
                else:
                    failures.append({"line": case["line"], "field": f, "expected": want, "actual": got})
    return {
        "cases": len(cases),
        "accuracy": {f: round(hits[f] / total[f], 3) for f in total},
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tt-eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translator", help="evaluate the structured parser against a golden file")
    t.add_argument("--golden", type=Path, required=True)
    t.add_argument("--subset", default=None)
    args = ap.parse_args(argv)
    report = evaluate(load(args.golden), subset=args.subset)
    print(f"cases: {report['cases']}")
    for f, acc in sorted(report["accuracy"].items()):
        print(f"  {f:<12} {acc:.3f}")
    for fail in report["failures"][:20]:
        print("  FAIL", fail)
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
