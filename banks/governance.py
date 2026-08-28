"""MOD-04 Governance: daily channel caps, collision protection, cadence queue,
funnel tracking.

Caps: email 40/day, LinkedIn 20/day. Overflow queues to next day, never dropped.
Collision: 'got a reply' freezes all pending outreach at that company.
Cadence: Day 3/7/14 from sent_at. Stops on reply, 3 touches, or interviewing/closed.
"""
from __future__ import annotations

import datetime

from .store import cursor, transaction

DAILY_CAPS: dict[str, int] = {"email": 40, "linkedin": 20}
CONTACT_SPACING_DAYS = 14


# ---------------------------------------------------------------------------
# Daily caps
# ---------------------------------------------------------------------------

def check_and_increment(db_path: str, channel: str, date: str) -> bool:
    """Return True if under cap and increment the counter; False if at/over cap."""
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
    """Record 'got a reply': freeze company, freeze pending cadence, log funnel event.

    All three writes are atomic — a crash must not leave the company frozen
    but cadence still pending (which would surface outreach to a live conversation).
    """
    with cursor(db_path) as cur:
        opp = cur.execute(
            "SELECT company_normalized FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()

    company = opp["company_normalized"] if opp else None
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with transaction(db_path) as cur:
        if company:
            cur.execute(
                "INSERT INTO company_freeze (company_normalized, frozen_at, reason, thaw_at) "
                "VALUES (?, ?, 'got_reply', NULL) "
                "ON CONFLICT(company_normalized) DO UPDATE SET "
                "frozen_at = excluded.frozen_at, reason = excluded.reason, "
                "thaw_at = excluded.thaw_at",
                (company, now),
            )
            cur.execute(
                """UPDATE cadence_queue SET status = 'frozen'
                   WHERE status = 'pending'
                   AND outreach_lane_id IN (
                       SELECT id FROM outreach_lanes WHERE opportunity_id = ?
                   )""",
                (opportunity_id,),
            )
        cur.execute(
            "INSERT INTO funnel_events (opportunity_id, event_type, ts) VALUES (?, ?, ?)",
            (opportunity_id, "replied", now),
        )


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


def record_interview(db_path: str, opportunity_id: int) -> None:
    """Called by the 'Interview/Offer' Slack button — interview outcome."""
    record_funnel_event(db_path, opportunity_id, "interview")


def record_offer(db_path: str, opportunity_id: int) -> None:
    """Called by the 'Interview/Offer' Slack button — offer outcome."""
    record_funnel_event(db_path, opportunity_id, "offer")


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

def queue_cadence(
    db_path: str, outreach_lane_id: int, sent_date: str | None = None
) -> None:
    """Queue 3 follow-up touches (Day 3/7/14) for a sent lane.

    Reads sent_at from outreach_lanes if sent_date not supplied — ensures cadence
    is keyed off the actual sent timestamp, not a caller-supplied guess.
    """
    from .cadence import FOLLOW_UP_DAYS
    if sent_date is None:
        with cursor(db_path) as cur:
            row = cur.execute(
                "SELECT sent_at FROM outreach_lanes WHERE id = ?", (outreach_lane_id,)
            ).fetchone()
        sent_date = (
            (row["sent_at"] if row and row["sent_at"] else None)
            or datetime.datetime.now(datetime.timezone.utc).isoformat()
        )[:10]
    base = datetime.date.fromisoformat(sent_date)
    with cursor(db_path) as cur:
        for i, delta in enumerate(FOLLOW_UP_DAYS, start=1):
            due = (base + datetime.timedelta(days=delta)).isoformat()
            cur.execute(
                "INSERT OR IGNORE INTO cadence_queue "
                "(outreach_lane_id, touch_number, due_date) VALUES (?, ?, ?)",
                (outreach_lane_id, i, due),
            )


def mark_lane_sent(db_path: str, lane_id: int) -> None:
    """Record sent_at on the lane so queue_cadence can key off it."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE outreach_lanes SET sent_at = ?, status = 'sent' WHERE id = ?",
            (now, lane_id),
        )


def due_cadence_touches(db_path: str, today: str) -> list[dict]:
    """Return pending cadence touches due on or before today.

    Skips touches for opportunities already in interviewing or closed status
    (cadence is pointless once we're in conversation or done).
    """
    with cursor(db_path) as cur:
        rows = cur.execute(
            """SELECT cq.*, ol.opportunity_id, ol.lane_type, ol.contact_id
               FROM cadence_queue cq
               JOIN outreach_lanes ol ON ol.id = cq.outreach_lane_id
               JOIN opportunities o ON o.id = ol.opportunity_id
               WHERE cq.status = 'pending' AND cq.due_date <= ?
               AND o.status NOT IN ('interviewing', 'closed')
               ORDER BY cq.due_date""",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 14-day per-contact spacing
# ---------------------------------------------------------------------------

def check_14day_spacing(db_path: str, contact_id: int, today: str) -> bool:
    """True if safe to contact (14+ days since last outreach to this contact).

    Queries outreach_lanes.sent_at keyed by contact_id — avoids the fragile
    name/email match that touch_log required.
    """
    cutoff = (
        datetime.date.fromisoformat(today)
        - datetime.timedelta(days=CONTACT_SPACING_DAYS)
    ).isoformat()
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT MAX(sent_at) last_sent FROM outreach_lanes "
            "WHERE contact_id = ? AND sent_at IS NOT NULL",
            (contact_id,),
        ).fetchone()
    if not row or not row["last_sent"]:
        return True
    return row["last_sent"][:10] <= cutoff


# ---------------------------------------------------------------------------
# Network Activation Lite (MOD-03 — daily relationship surfacing)
# ---------------------------------------------------------------------------

_DECISION_MAKER_KEYWORDS = ("director", "vp", "head", "chief", "cro", "ceo", "cmo", "coo")


def network_activation_due(
    db_path: str, today: str, limit: int = 3
) -> list[dict]:
    """Return up to `limit` contacts untouched for 14+ days, ranked by seniority.

    Ranking: decision-maker titles first, then recruiter source, then rest.
    "Untouched" = no outreach_lane.sent_at in the last 14 days.
    """
    cutoff = (
        datetime.date.fromisoformat(today)
        - datetime.timedelta(days=CONTACT_SPACING_DAYS)
    ).isoformat() + "T00:00:00"

    with cursor(db_path) as cur:
        rows = cur.execute(
            """SELECT c.*,
               COALESCE(MAX(ol.sent_at), '') last_sent
               FROM contacts c
               LEFT JOIN outreach_lanes ol ON ol.contact_id = c.id AND ol.sent_at IS NOT NULL
               GROUP BY c.id
               HAVING last_sent = '' OR last_sent <= ?
               ORDER BY c.id""",
            (cutoff,),
        ).fetchall()

    contacts = [dict(r) for r in rows]

    def _rank(c: dict) -> int:
        title = (c.get("title") or "").lower()
        if any(k in title for k in _DECISION_MAKER_KEYWORDS):
            return 0
        if c.get("source") in ("recruiter_registry",):
            return 1
        return 2

    contacts.sort(key=_rank)
    return contacts[:limit]


# ---------------------------------------------------------------------------
# Secondary escalation query (populated by stall_aged_warm_intros)
# ---------------------------------------------------------------------------

def pending_secondary_escalations(db_path: str) -> list[dict]:
    """Return outreach_lanes rows representing stall-triggered secondary escalations."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT ol.*, o.title opp_title, o.company_normalized "
            "FROM outreach_lanes ol "
            "JOIN opportunities o ON o.id = ol.opportunity_id "
            "WHERE ol.lane_type = 'secondary_escalation' AND ol.status = 'pending' "
            "ORDER BY ol.created_at",
        ).fetchall()
        return [dict(r) for r in rows]
