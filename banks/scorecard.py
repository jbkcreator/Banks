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
            f"${(row.get('money_found_cents') or 0) / 100:,.0f}",
            "$",
            False,
        ),
    ]
    return lines


def count_reds(lines: list[ScorecardLine]) -> int:
    return sum(1 for line in lines if line.red)


# ---------------------------------------------------------------------------
# "Plus:" block (T2-6): applications, maintenance, own-line, today's find.

@dataclass(frozen=True)
class PlusBlock:
    applications_queued: int
    applications_submitted: int      # always 0 by construction; displayed for transparency
    maintenance_over_7d: int
    tasks_today: int
    drafts_approved: int
    drafts_corrected: int
    misses_owned: int
    todays_find: str | None          # title from daily_finds, or None


def render_plus_block(db_path: str, week_ending: str) -> PlusBlock:
    """The scorecard 'Plus:' own-line, read from live tables."""
    from .find import get_find
    import datetime as _dt

    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT * FROM scorecard_weekly WHERE week_ending = ?", (week_ending,)
        ).fetchone()
        row = dict(row) if row else {}

        maintenance_over_7d = cur.execute(
            "SELECT COUNT(*) AS n FROM maintenance_tickets "
            "WHERE status != 'closed' AND opened_at <= date(?, '-7 day')",
            (week_ending,),
        ).fetchone()["n"]

        # Corrections this week = drafts that got a REVISE.
        week_start = (
            _dt.date.fromisoformat(week_ending) - _dt.timedelta(days=6)
        ).isoformat()
        drafts_corrected = cur.execute(
            "SELECT COUNT(*) AS n FROM corrections c "
            "JOIN decision_packets p ON p.id = c.packet_id "
            "WHERE p.created_at >= ? AND p.created_at <= ?",
            (week_start, week_ending + "T23:59:59"),
        ).fetchone()["n"]

        misses_owned = cur.execute(
            "SELECT COUNT(*) AS n FROM weekly_misses WHERE week_ending = ?",
            (week_ending,),
        ).fetchone()["n"]

    find = get_find(db_path, week_ending)
    return PlusBlock(
        applications_queued=row.get("applications_queued", 0),
        applications_submitted=row.get("applications_submitted", 0),
        maintenance_over_7d=maintenance_over_7d,
        tasks_today=row.get("applications_queued", 0),  # placeholder until task table exists
        drafts_approved=row.get("applications_submitted", 0),  # use scorecard col for now
        drafts_corrected=drafts_corrected,
        misses_owned=misses_owned,
        todays_find=find.title if find and find.kind != "none" else None,
    )


# ---------------------------------------------------------------------------
# Weekly biggest-miss (T2-12)

def record_miss(db_path: str, week_ending: str, miss: str) -> None:
    """Record the named biggest miss for a week. Required every week."""
    if not miss or not miss.strip():
        raise ValueError("miss text is required — absence of misses for a month "
                         "is a reporting failure, not a clean sheet")
    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO weekly_misses (week_ending, miss, owned_at) "
            "VALUES (?, ?, ?)",
            (week_ending, miss.strip(), now),
        )


def missing_miss_weeks(db_path: str, recent_weeks: list[str]) -> list[str]:
    """Return weeks in recent_weeks that have no miss recorded."""
    with cursor(db_path) as cur:
        recorded = {
            r["week_ending"] for r in cur.execute(
                "SELECT week_ending FROM weekly_misses"
            ).fetchall()
        }
    return [w for w in recent_weeks if w not in recorded]


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
