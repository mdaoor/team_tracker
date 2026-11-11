"""Export stamping (Phase 4 §4.9; router coverage: none yet -- the CSV/print endpoints arrive in P6/P7)."""

from __future__ import annotations

from datetime import UTC, datetime

from tt_server.acl.export import csv_export, is_stamped, print_footer, stamp

ROUTERS_COVERED: tuple[str, ...] = ()


def test_stamp_format() -> None:
    at = datetime(2026, 10, 23, 9, 30, tzinfo=UTC)
    assert (
        stamp("@sara", "project WEB", at) == "Exported by @sara · scope project WEB · at 2026-10-23T09:30:00Z"
    )
    assert stamp("sara", "workspace", at).startswith("Exported by @sara")
    assert print_footer("@sara", "team squad-b", at).endswith("at 2026-10-23T09:30:00Z")


def test_csv_is_stamped_at_both_ends_and_neutralises_formulas() -> None:
    out = csv_export("@sara", "project WEB", ["key", "title"], [["WEB-1", "=HYPERLINK(1)"], ["WEB-2", "ok"]])
    assert is_stamped(out)
    lines = out.splitlines()
    assert lines[0].startswith("# Exported by @sara") and lines[-1] == lines[0]
    assert "'=HYPERLINK(1)" in out and "WEB-2,ok" in out
    assert not is_stamped("key,title\nWEB-1,x\n")
    assert not is_stamped("")
