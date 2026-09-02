"""MOD-05 queue actions — Skip / Snooze / Mark done, and carry-over helpers.

These manage the queue's own view-state in `queue_items` (Q3/Q10/Q19).
Approve / Reject / Revise stay on the existing approval.apply_action path;
these three are the queue-specific verbs:

  Snooze  — time-based: hide until snooze_until (default next morning), then
            it re-enters via the carried-over section.
  Skip    — today-only: drop from the queue, no auto-resurface.
  Mark done — a manual LinkedIn/Call/Text action Josh performed himself; reuses
            the MARK_SENT semantics (packet answered+completed, intent
            suppressed) and feeds cadence + funnel + touch_log so the pipeline
            reflects the real action.
"""
from __future__ import annotations

import datetime
from datetime import date, timedelta

from .governance import mark_lane_sent, queue_cadence, record_funnel_event
from .packets import mark_answered, mark_completed
from .refs import DraftRef
from .relay import suppress_intent
from .clock import local_date_plus
from .store import cursor, transaction


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def snooze_item(db_path: str, draft_ref: DraftRef | str, days: int = 1) -> None:
    """Hold until `days` from today (default next morning). Re-enters carried-over."""
    # Josh's calendar, not the server's: at 9pm ET the UTC date is already
    # tomorrow, so date.today()+1 silently snoozed for two days.
    until = local_date_plus(days)
    _ensure_row(db_path, draft_ref)
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE queue_items SET state = 'snoozed', snooze_until = ? WHERE draft_ref = ?",
            (until, str(DraftRef.parse(draft_ref))),
        )


def skip_item(db_path: str, draft_ref: DraftRef | str) -> None:
    """Drop from today's queue; does NOT auto-resurface (unlike snooze)."""
    _ensure_row(db_path, draft_ref)
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE queue_items SET state = 'skipped' WHERE draft_ref = ?",
            (str(DraftRef.parse(draft_ref)),),
        )


def mark_done(db_path: str, draft_ref: DraftRef | str) -> None:
    """Josh did a manual action (LinkedIn DM / call / text). MARK_SENT semantics.

    ONE transaction, deliberately. This used to be six separate writes, and a
    crash between them could leave the send intent un-suppressed while the
    packet read as answered — Relay would then email a contact Josh had already
    messaged himself. That double-contact is precisely what the collision ledger
    exists to prevent, so the suppression and the state change commit together
    or not at all (fixed 2026-09-02).

    Writes: packet answered+completed, intent suppressed, lane sent_at + status,
    Day 3/7/14 cadence, 'contacted' funnel event, touch_log, queue_items='done'.
    """
    from .cadence import FOLLOW_UP_DAYS

    ref = DraftRef.parse(draft_ref)
    now = _now_iso()
    _ensure_row(db_path, ref)

    with transaction(db_path) as cur:
        lane = cur.execute(
            "SELECT id, opportunity_id FROM outreach_lanes WHERE draft_ref = ?",
            (str(ref),),
        ).fetchone()
        si = cur.execute(
            "SELECT to_addr FROM send_intents WHERE draft_ref = ?", (str(ref),)
        ).fetchone()

        # Suppress FIRST: if anything below fails the whole transaction rolls
        # back, so Relay can never inherit a half-applied manual send.
        cur.execute(
            "UPDATE send_intents SET status = 'suppressed' "
            "WHERE draft_ref = ? AND status NOT IN ('sent', 'suppressed')",
            (str(ref),),
        )
        cur.execute("UPDATE decision_packets SET answered_at = ?, completed_at = ? "
                    "WHERE id = ?", (now, now, ref.packet_id))

        if lane:
            cur.execute(
                "UPDATE outreach_lanes SET sent_at = ?, status = 'sent' WHERE id = ?",
                (now, lane["id"]),
            )
            base = datetime.date.fromisoformat(now[:10])
            for i, delta in enumerate(FOLLOW_UP_DAYS, start=1):
                cur.execute(
                    "INSERT OR IGNORE INTO cadence_queue "
                    "(outreach_lane_id, touch_number, due_date) VALUES (?, ?, ?)",
                    (lane["id"], i, (base + datetime.timedelta(days=delta)).isoformat()),
                )
            if lane["opportunity_id"]:
                cur.execute(
                    "INSERT INTO funnel_events (opportunity_id, event_type, ts) "
                    "VALUES (?, 'contacted', ?)", (lane["opportunity_id"], now),
                )

        if si and si["to_addr"]:
            cur.execute(
                "INSERT INTO touch_log (address, draft_ref, touched_at) VALUES (?, ?, ?)",
                (si["to_addr"], str(ref), now),
            )
        cur.execute(
            "UPDATE queue_items SET state = 'done' WHERE draft_ref = ?", (str(ref),)
        )


# ---------------------------------------------------------------------------
# Carry-over helpers (read-only; build_sections uses the same shape)
# ---------------------------------------------------------------------------

def carried_over_items(db_path: str, today: str) -> list[dict]:
    """Active items first surfaced before today — the carry-forward set."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT * FROM queue_items "
            "WHERE state = 'active' AND substr(first_surfaced_at, 1, 10) < ? "
            "ORDER BY first_surfaced_at",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


def due_snoozed_items(db_path: str, today: str) -> list[dict]:
    """Snoozed items whose snooze_until has arrived — eligible to re-enter."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT * FROM queue_items "
            "WHERE state = 'snoozed' AND snooze_until <= ? "
            "ORDER BY snooze_until",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


def _ensure_row(db_path: str, draft_ref: DraftRef | str) -> None:
    """Create a queue_items row if the action arrives before the item was posted."""
    now = _now_iso()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO queue_items "
            "(draft_ref, category, state, first_surfaced_at, last_surfaced_at) "
            "VALUES (?, '', 'active', ?, ?)",
            (str(DraftRef.parse(draft_ref)), now, now),
        )
