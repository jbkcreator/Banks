"""Weekly scorecard + morning dashboard renderers (Part 5).

All targets are spec-given (not client-dependent): occupancy 100%, vacancy
days trending down, inquiries answered <1h >=95%, applications from inquiries
>=40%, collections on time >=95%, bills on time 100%, plus reviews and money
found. Renders against whatever is in the store — seeded data today, real
data once client sources are wired in.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import cursor

# Spec-given targets (Part 5 weekly scorecard). Not derived from client data.
TARGETS = {
    "occupancy_pct": 100.0,
    "inquiries_answered_under_1h_pct": 95.0,
    "applications_from_inquiries_pct": 40.0,
    "collections_on_time_pct": 95.0,
    "bills_on_time_pct": 100.0,
}


@dataclass(frozen=True)
class ScorecardLine:
    label: str
    value: str
    target: str
    red: bool


def _red(value: float | None, target: float, floor: bool = True) -> bool:
    if value is None:
        return True
    return value < target if floor else value > target


def render_weekly_scorecard(db_path: str, week_ending: str) -> list[ScorecardLine]:
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM scorecard_weekly WHERE week_ending = ?", (week_ending,)
        )
        row = cur.fetchone()

    row = dict(row) if row else {}
    lines = [
        ScorecardLine(
            "Occupancy",
            f"{row.get('occupancy_pct', 0):.0f}%",
            "100%",
            _red(row.get("occupancy_pct"), TARGETS["occupancy_pct"]),
        ),
        ScorecardLine(
            "Vacancy days",
            str(row.get("vacancy_days", "—")),
            "↓",
            False,  # trend-based, not a floor — evaluated by comparison over time
        ),
        ScorecardLine(
            "Inquiries answered <1h",
            f"{row.get('inquiries_answered_under_1h_pct', 0):.0f}%",
            "≥95%",
            _red(row.get("inquiries_answered_under_1h_pct"), TARGETS["inquiries_answered_under_1h_pct"]),
        ),
        ScorecardLine(
            "Applications from inquiries",
            f"{row.get('applications_from_inquiries_pct', 0):.0f}%",
            "≥40%",
            _red(row.get("applications_from_inquiries_pct"), TARGETS["applications_from_inquiries_pct"]),
        ),
        ScorecardLine(
            "Collections on time",
            f"{row.get('collections_on_time_pct', 0):.0f}%",
            "≥95%",
            _red(row.get("collections_on_time_pct"), TARGETS["collections_on_time_pct"]),
        ),
        ScorecardLine(
            "Bills on time",
            f"{row.get('bills_on_time_pct', 0):.0f}%",
            "100%",
            _red(row.get("bills_on_time_pct"), TARGETS["bills_on_time_pct"]),
        ),
        ScorecardLine(
            "Reviews requested/received",
            f"{row.get('reviews_requested', 0)}/{row.get('reviews_received', 0)}",
            "n",
            False,
        ),
        ScorecardLine(
            "Money found this week",
            f"${row.get('money_found_cents', 0) / 100:,.0f}",
            "$",
            False,
        ),
    ]
    return lines


def count_reds(lines: list[ScorecardLine]) -> int:
    return sum(1 for line in lines if line.red)


def render_morning_dashboard(db_path: str, as_of: str) -> str:
    """Part 5 job 1: one screen, 60-second scan.

    yesterday recap · today's 1-3 pre-ranked · rooms/properties · money due
    7-day window · schedule + prep · one learning item · scorecard line.
    """
    with cursor(db_path) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM rooms WHERE occupied = 0")
        vacant = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM rooms")
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT name, amount_cents, due_date FROM bills "
            "WHERE due_date <= date('now', '+7 day') ORDER BY due_date ASC"
        )
        money_due = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT decision, dollar_impact_cents FROM decision_packets "
            "WHERE answered_at IS NOT NULL AND completed_at IS NULL "
            "ORDER BY dollar_impact_cents DESC LIMIT 3"
        )
        top_actions = [dict(r) for r in cur.fetchall()]

    lines = [f"*Banks — Morning Dashboard ({as_of})*", ""]
    lines.append(f"Occupancy: {total - vacant}/{total} occupied")
    if top_actions:
        lines.append("Today's top actions:")
        for a in top_actions:
            impact = f"${(a['dollar_impact_cents'] or 0) / 100:,.0f}"
            lines.append(f"  • {a['decision']} ({impact})")
    else:
        lines.append("Today's top actions: none pending")
    if money_due:
        lines.append("Money due (7-day window):")
        for b in money_due:
            lines.append(f"  • {b['name']}: ${(b['amount_cents'] or 0)/100:,.0f} due {b['due_date']}")
    else:
        lines.append("Money due (7-day window): none")
    return "\n".join(lines)
