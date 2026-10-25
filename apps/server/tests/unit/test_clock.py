"""Phase 13 R3.5: business dates come from the workspace/person zone, never the server's."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

from tt_server.clock import person_today, today_in, utc_today, ws_today

LATE_UTC = datetime(
    2026, 10, 23, 22, 30, tzinfo=UTC
)  # already Saturday 01:30 in Riyadh, still Friday in New York


def test_the_same_instant_is_different_days_in_different_zones() -> None:
    assert today_in("Asia/Riyadh", LATE_UTC) == date(2026, 10, 24)
    assert today_in("America/New_York", LATE_UTC) == date(2026, 10, 23)
    assert utc_today(LATE_UTC) == date(2026, 10, 23)


def test_workspace_and_person_helpers_and_bad_zone_fallback() -> None:
    assert ws_today(SimpleNamespace(settings={"time_zone": "Asia/Riyadh"}), LATE_UTC) == date(2026, 10, 24)
    assert ws_today(SimpleNamespace(settings=None), LATE_UTC) == date(2026, 10, 23)
    assert person_today(SimpleNamespace(time_zone="Asia/Tokyo"), LATE_UTC) == date(2026, 10, 24)
    assert today_in("Mars/Olympus", LATE_UTC) == date(2026, 10, 23)


def test_no_server_local_today_in_the_server_package() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "tt_server"
    bad = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if re.search(r"\bdate\.today\(\)|datetime\.now\(\)|datetime\.utcnow\(\)", p.read_text())
    ]
    assert bad == []
