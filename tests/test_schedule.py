from datetime import datetime

from banks.schedule import (
    CalendarEvent,
    OpportunityCostInputs,
    find_conflicts,
    roi_line,
    weekly_roi,
)


def test_find_conflicts_detects_overlap():
    a = CalendarEvent("Property showing", datetime(2026, 8, 5, 14, 0), datetime(2026, 8, 5, 15, 0))
    b = CalendarEvent("Vendor call", datetime(2026, 8, 5, 14, 30), datetime(2026, 8, 5, 15, 30))
    c = CalendarEvent("Dinner", datetime(2026, 8, 5, 19, 0), datetime(2026, 8, 5, 20, 0))

    conflicts = find_conflicts([a, b, c])

    assert len(conflicts) == 1
    assert {conflicts[0][0].title, conflicts[0][1].title} == {"Property showing", "Vendor call"}


def test_find_conflicts_empty_when_no_overlap():
    a = CalendarEvent("Morning", datetime(2026, 8, 5, 9, 0), datetime(2026, 8, 5, 10, 0))
    b = CalendarEvent("Afternoon", datetime(2026, 8, 5, 14, 0), datetime(2026, 8, 5, 15, 0))

    assert find_conflicts([a, b]) == []


def test_weekly_roi_proves_return_when_positive():
    inputs = OpportunityCostInputs(hours_saved=10, hourly_value_cents=10000, monthly_cost_cents=50000)
    result = weekly_roi(inputs)

    assert result["proves_return"] is True
    assert result["value_returned_cents"] == 100000
    line = roi_line(result)
    assert "net +" in line


def test_weekly_roi_flags_gap_when_negative():
    inputs = OpportunityCostInputs(hours_saved=1, hourly_value_cents=5000, monthly_cost_cents=50000)
    result = weekly_roi(inputs)

    assert result["proves_return"] is False
    line = roi_line(result)
    assert "Flagging the gap" in line
