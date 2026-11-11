from __future__ import annotations

from datetime import datetime

from tt_server.domain.capture.dailylog import due_now


def test_due_window() -> None:
    d = datetime(2026, 10, 23, 17, 30)
    assert due_now(d, "17:30") and due_now(d.replace(minute=44), "17:30")
    assert not due_now(d.replace(minute=45), "17:30") and not due_now(d.replace(minute=29), "17:30")
