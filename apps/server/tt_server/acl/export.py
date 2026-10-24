"""Export stamping (Phase 4 §4.9, UX §6): every CSV / printable export says who took it, of what, and when.

The CSV/print endpoints arrive in Phases 6/7; they must build their output through these helpers so the stamp
cannot be forgotten. `is_stamped` is the check their tests (and `tests/api/test_acl_export.py`) use.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from tt_schema.base import utcnow

STAMP_RE = re.compile(r"Exported by @\S+ · scope .+ · at \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def stamp(handle: str, scope: str, at: datetime | None = None) -> str:
    """`Exported by @sara · scope project WEB · at 2026-10-23T09:30:00Z`."""
    at = at or utcnow()
    handle = handle if handle.startswith("@") else "@" + handle
    return f"Exported by {handle} · scope {scope} · at {at.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def csv_export(
    handle: str, scope: str, header: Sequence[str], rows: Iterable[Sequence[Any]], at: datetime | None = None
) -> str:
    """CSV with the stamp as a leading `#` comment line and again as the last line (survives head/tail)."""
    line = stamp(handle, scope, at)
    out = io.StringIO()
    out.write(f"# {line}\n")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    w.writerows(_safe(r) for r in rows)
    out.write(f"# {line}\n")
    return out.getvalue()


def print_footer(handle: str, scope: str, at: datetime | None = None) -> str:
    """Text for the `@media print` footer."""
    return stamp(handle, scope, at)


def is_stamped(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return bool(lines) and all(STAMP_RE.search(ln) for ln in (lines[0], lines[-1]))


def _safe(row: Sequence[Any]) -> list[Any]:
    """Neutralise spreadsheet formula injection in text cells."""
    return ["'" + c if isinstance(c, str) and c and c[0] in "=+-@\t\r" else c for c in row]
