"""CalendarPort — window filtering, read-only by construction, conflict feed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from banks.calendarport import CalendarPort, FakeCalendarPort, GoogleCalendarPort
from banks.schedule import CalendarEvent, find_conflicts


def _ev(title, h0, h1, owner="you"):
    base = datetime(2026, 8, 6, tzinfo=timezone.utc)
    return CalendarEvent(title, base + timedelta(hours=h0), base + timedelta(hours=h1), owner)


def test_events_filtered_to_window():
    port = FakeCalendarPort([_ev("early", 1, 2), _ev("in", 10, 11)])
    base = datetime(2026, 8, 6, tzinfo=timezone.utc)
    got = port.events(base + timedelta(hours=9), base + timedelta(hours=12))
    assert [e.title for e in got] == ["in"]


def test_personal_block_gets_equal_weight_in_conflicts():
    # A business call overlapping a family block must flag (Q23 equal weight).
    events = [_ev("Investor call", 10, 11, owner="you"),
              _ev("Family time", 10, 12, owner="family")]
    conflicts = find_conflicts(events)
    assert len(conflicts) == 1


def test_calendarport_is_read_only_by_construction():
    # No write method exists on the port or the live adapter (Q23).
    for name in ("create", "insert", "update", "delete", "patch"):
        assert not hasattr(GoogleCalendarPort, name)
        assert not hasattr(FakeCalendarPort, name)
    # the protocol surface is exactly events()
    assert [m for m in dir(CalendarPort) if not m.startswith("_")] == ["events"]
