"""MOD-04 Governance: daily channel caps, collision protection, cadence queue,
funnel tracking.

Caps: email 40/day, LinkedIn 20/day. Overflow queues to next day, never dropped.
Collision: 'got a reply' freezes all pending outreach at that company.
Cadence: Day 3/7/14 from sent_at. Stops on reply, 3 touches, or interviewing/closed.
14-day per-contact spacing enforced via touch_log (already in flow.propose).
"""
from __future__ import annotations

import datetime

from .store import cursor

DAILY_CAPS: dict[str, int] = {"email": 40, "linkedin": 20}
CONTACT_SPACING_DAYS = 14


# ---------------------------------------------------------------------------
# Daily caps
# ---------------------------------------------------------------------------

def check_and_increment(db_path: str, channel: str, date: str) -> bool:
    """Return True if under cap and increment the counter; False if at/over cap.

    Reads before writing so the decision (allow / deny) is unambiguous.
    """
    cap = DAILY_CAPS.get(channel)
    if cap is None:
        return True  # uncapped channel
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT count FROM governance_ledger WHERE date = ? AND channel = ?",
            (date, channel),
        ).fetchone()
        current = row["count"] if row else 0
        if current >= cap:
            return False
        cur.execute(
            "INSERT INTO governance_ledger (date, channel, count) VALUES (?, ?, 1) "
            "ON CONFLICT(date, channel) DO UPDATE SET count = count + 1",
            (date, channel),
        )
        return True


# ---------------------------------------------------------------------------
# Company freeze (collision protection)
# ---------------------------------------------------------------------------

def is_company_frozen(db_path: str, company_normalized: str) -> bool:
    """True if the company has an active freeze (got-reply signal)."""
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT thaw_at FROM company_freeze WHERE company_normalized = ?",
            (company_normalized,),
        ).fetchone()
    if not row:
        return False
    if row["thaw_at"] is None:
        return True  # permanent / manual-thaw only
    return datetime.datetime.now(datetime.timezone.utc).isoformat() < row["thaw_at"]


# Alias for surround.py import
is_contact_frozen = is_company_frozen


def freeze_company(
    db_path: str,
    company_normalized: str,
    reason: str = "got_reply",
    thaw_after_days: int | None = None,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    thaw_at = None
    if thaw_after_days is not None:
        thaw_at = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=thaw_after_days)
        ).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO company_freeze (company_normalized, frozen_at, reason, thaw_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(company_normalized) DO UPDATE SET "
            "frozen_at = excluded.frozen_at, reason = excluded.reason, "
            "thaw_at = excluded.thaw_at",
            (company_normalized, now, reason, thaw_at),
        )


# ---------------------------------------------------------------------------
# Got-reply signal (MOD-04 collision protection)
# ---------------------------------------------------------------------------

def got_reply(db_path: str, opportunity_id: int) -> None:
    """Record 'got a reply' signal.

    1. Freeze the company so no new outreach is drafted.
    2. Freeze all pending cadence touches for this opportunity.
    3. Log a 'replied' funnel event.
    """
    with cursor(db_path) as cur:
        opp = cur.execute(
            "SELECT company_normalized FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()

    if opp and opp["company_normalized"]:
        freeze_company(db_path, opp["company_normalized"], reason="got_reply")
        with cursor(db_path) as cur:
            cur.execute(
                """UPDATE cadence_queue SET status = 'frozen'
                   WHERE status = 'pending'
                   AND outreach_lane_id IN (
                       SELECT id FROM outreach_lanes WHERE opportunity_id = ?
                   )""",
                (opportunity_id,),
            )

    record_funnel_event(db_path, opportunity_id, "replied")


# ---------------------------------------------------------------------------
# Funnel events
# ---------------------------------------------------------------------------

def record_funnel_event(db_path: str, opportunity_id: int, event_type: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO funnel_events (opportunity_id, event_type, ts) VALUES (?, ?, ?)",
            (opportunity_id, event_type, now),
        )


def weekly_funnel_summary(db_path: str, week_ending: str) -> dict:
    """Return funnel counts for the 7-day window ending on `week_ending` (ISO date)."""
    week_start = (
        datetime.date.fromisoformat(week_ending) - datetime.timedelta(days=6)
    ).isoformat()
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT event_type, COUNT(*) n FROM funnel_events "
            "WHERE ts >= ? AND ts <= ? GROUP BY event_type",
            (week_start + "T00:00:00", week_ending + "T23:59:59"),
        ).fetchall()
    return {r["event_type"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Cadence queue
# ---------------------------------------------------------------------------

def queue_cadence(db_path: str, outreach_lane_id: int, sent_date: str) -> None:
    """Queue the 3 follow-up touches (Day 3/7/14) for a newly-sent lane."""
    from .cadence import FOLLOW_UP_DAYS
    base = datetime.date.fromisoformat(sent_date)
    with cursor(db_path) as cur:
        for i, delta in enumerate(FOLLOW_UP_DAYS, start=1):
            due = (base + datetime.timedelta(days=delta)).isoformat()
            cur.execute(
                "INSERT OR IGNORE INTO cadence_queue "
                "(outreach_lane_id, touch_number, due_date) VALUES (?, ?, ?)",
                (outreach_lane_id, i, due),
            )


def due_cadence_touches(db_path: str, today: str) -> list[dict]:
    """Return all pending cadence touches due on or before today."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            """SELECT cq.*, ol.opportunity_id, ol.lane_type, ol.contact_id
               FROM cadence_queue cq
               JOIN outreach_lanes ol ON ol.id = cq.outreach_lane_id
               WHERE cq.status = 'pending' AND cq.due_date <= ?
               ORDER BY cq.due_date""",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 14-day per-contact spacing (supplements flow.propose touch_log gate)
# ---------------------------------------------------------------------------

def check_14day_spacing(db_path: str, contact_id: int, today: str) -> bool:
    """True if it's safe to contact this person (14+ days since last touch)."""
    cutoff = (
        datetime.date.fromisoformat(today)
        - datetime.timedelta(days=CONTACT_SPACING_DAYS)
    ).isoformat()
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT touched_at FROM touch_log "
            "WHERE address = (SELECT COALESCE(email, name) FROM contacts WHERE id = ?) "
            "ORDER BY touched_at DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
    if not row:
        return True
    return row["touched_at"][:10] <= cutoff
