"""Family & schedule (Part 5 job 7) + opportunity-cost meter (job 10).

Calendar access itself (which calendars, read-only share — Q33) is
client-pending; this module works off whatever events land in-memory or in
`fact_freshness`, so conflict-detection logic is buildable and testable now.
Read-only by construction: no function here creates or edits an event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enforcement import Draft
from .selfheal import read_fact, record_fact


@dataclass(frozen=True)
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    owner: str = "you"


def find_conflicts(events: list[CalendarEvent]) -> list[tuple[CalendarEvent, CalendarEvent]]:
    """Pure overlap detection — read-only, never mutates a calendar."""
    conflicts = []
    sorted_events = sorted(events, key=lambda e: e.start)
    for i, a in enumerate(sorted_events):
        for b in sorted_events[i + 1 :]:
            if b.start >= a.end:
                break
            conflicts.append((a, b))
    return conflicts


def conflict_draft(a: CalendarEvent, b: CalendarEvent) -> Draft:
    return Draft(
        kind="calendar_conflict",
        to="you",
        subject="Schedule conflict flagged",
        body=f"'{a.title}' ({a.start:%I:%M%p}) overlaps '{b.title}' ({b.start:%I:%M%p}).",
    )


def record_conflict(db_path: str, key: str, a: CalendarEvent, b: CalendarEvent) -> None:
    record_fact(db_path, key, "bill", f"Overlap: '{a.title}' vs '{b.title}'")  # always-current kind


def occasion_draft(name: str, occasion: str, days_out: int) -> Draft:
    return Draft(
        kind="occasion_reminder",
        to="you",
        subject=f"Coming up: {name}'s {occasion}",
        body=f"{days_out} days out — {name}'s {occasion}.",
    )


# --- Opportunity-cost meter (job 10) ----------------------------------------


@dataclass(frozen=True)
class OpportunityCostInputs:
    hours_saved: float
    hourly_value_cents: int   # Q35 — client figure
    monthly_cost_cents: int   # Q35 — client figure (service) + auto-tracked compute


def weekly_roi(inputs: OpportunityCostInputs) -> dict:
    value_returned_cents = int(inputs.hours_saved * inputs.hourly_value_cents)
    net_cents = value_returned_cents - inputs.monthly_cost_cents
    return {
        "hours_saved": inputs.hours_saved,
        "value_returned_cents": value_returned_cents,
        "monthly_cost_cents": inputs.monthly_cost_cents,
        "net_cents": net_cents,
        "proves_return": net_cents >= 0,
    }


def roi_line(result: dict) -> str:
    if result["proves_return"]:
        return (
            f"This week: {result['hours_saved']:.1f}h saved = "
            f"${result['value_returned_cents']/100:,.0f} value vs. "
            f"${result['monthly_cost_cents']/100:,.0f} cost — net +"
            f"${result['net_cents']/100:,.0f}."
        )
    return (
        f"This week: {result['hours_saved']:.1f}h saved = "
        f"${result['value_returned_cents']/100:,.0f} value vs. "
        f"${result['monthly_cost_cents']/100:,.0f} cost — gap of "
        f"${-result['net_cents']/100:,.0f}. Flagging the gap, as required."
    )
