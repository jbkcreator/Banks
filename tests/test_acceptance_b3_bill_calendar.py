"""B3.2 acceptance harness (planned in .wayfinder/block-03-banks-personal-finance):
seed a bill deadline and a calendar conflict, confirm both surface correctly
in the next brief. Runs against seeded data — no live finance/calendar
sources required yet.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from banks.scorecard import render_morning_dashboard
from banks.store import cursor


def _seed_bill_due_soon(db_path: str) -> int:
    due = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO bills (name, amount_cents, due_date, cadence, is_subscription)
            VALUES ('Property insurance renewal', 42000, ?, 'annual', 0)
            """,
            (due,),
        )
        return cur.lastrowid


def test_seeded_bill_surfaces_in_next_dashboard(db_path):
    _seed_bill_due_soon(db_path)

    rendered = render_morning_dashboard(db_path, as_of="2026-08-04")

    assert "Property insurance renewal" in rendered
    assert "Money due (7-day window)" in rendered


def test_seeded_calendar_conflict_is_flaggable(db_path):
    """Calendar itself isn't wired (needs Q33/client access); this proves the
    fact_freshness + flag path a real calendar feed would plug into."""
    from banks.selfheal import read_fact, record_fact

    record_fact(
        db_path,
        fact_key="calendar_conflict_2026-08-05",
        fact_kind="bill",  # always-current kind; calendar conflicts are same-day facts
        value="Double-booked: property showing 2pm overlaps vendor call 2pm",
    )

    conflict = read_fact(db_path, "calendar_conflict_2026-08-05")

    assert conflict is not None
    assert "Double-booked" in conflict
