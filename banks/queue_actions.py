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
from .store import cursor


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def snooze_item(db_path: str, draft_ref: DraftRef | str, days: int = 1) -> None:
    """Hold until `days` from today (default next morning). Re-enters carried-over."""
    until = (date.today() + timedelta(days=days)).isoformat()
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

    Marks the packet answered+completed, suppresses the send intent (Relay must
    never fire on a manual action), stamps the lane sent_at + starts its cadence,
    and logs touch_log + a 'contacted' funnel event so spacing/funnel are honest.
    """
    ref = DraftRef.parse(draft_ref)
    now = _now_iso()

    with cursor(db_path) as cur:
        lane = cur.execute(
            "SELECT id, opportunity_id FROM outreach_lanes WHERE draft_ref = ?",
            (str(ref),),
        ).fetchone()
        si = cur.execute(
            "SELECT to_addr FROM send_intents WHERE draft_ref = ?", (str(ref),)
        ).fetchone()

    mark_answered(db_path, ref.packet_id)
    mark_completed(db_path, ref.packet_id)
    suppress_intent(db_path, ref)

    if lane:
        mark_lane_sent(db_path, lane["id"])          # sent_at + status='sent'
        queue_cadence(db_path, lane["id"])            # Day 3/7/14 from sent_at
        if lane["opportunity_id"]:
            record_funnel_event(db_path, lane["opportunity_id"], "contacted")

    if si and si["to_addr"]:
        with cursor(db_path) as cur:
            cur.execute(
                "INSERT INTO touch_log (address, draft_ref, touched_at) VALUES (?, ?, ?)",
                (si["to_addr"], str(ref), now),
            )

    _ensure_row(db_path, ref)
    with cursor(db_path) as cur:
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
