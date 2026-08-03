"""Money truth: bills/renewals radar + subscription keep/kill memos (Part 5 job 4).

Track and remind ONLY — never pay, never transact. No function in this
module (or anywhere in Banks) moves money. The bill *source* (sheet, forwarded
emails, `#banks` message — Q25) is a client decision; this module works off
whatever lands in the `bills` table regardless of how it got there.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .enforcement import Draft
from .store import cursor


def bills_due_within(db_path: str, days: int) -> list[dict]:
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM bills WHERE due_date <= date('now', ?) AND due_date >= date('now') "
            "ORDER BY due_date ASC",
            (f"+{days} day",),
        )
        return [dict(r) for r in cur.fetchall()]


def nudge_draft(bill: dict, days_out: int) -> Draft:
    amount = (bill.get("amount_cents") or 0) / 100
    urgency = "due tomorrow" if days_out <= 1 else f"due in {days_out} days"
    return Draft(
        kind="bill_nudge",
        to="you",
        subject=f"Reminder — {bill['name']} {urgency}",
        body=f"{bill['name']}: ${amount:,.0f}, {urgency} ({bill['due_date']}). "
             f"Banks tracks and reminds only — you handle payment.",
    )


def due_nudges(db_path: str, now: date | None = None) -> list[Draft]:
    """The 7-day + 1-day nudge pass. Call from the scheduler's nightly job."""
    drafts = []
    for bill in bills_due_within(db_path, 7):
        due = datetime.fromisoformat(bill["due_date"]).date()
        today = now or datetime.now(timezone.utc).date()
        days_out = (due - today).days
        if days_out in (7, 1):
            drafts.append(nudge_draft(bill, days_out))
            with cursor(db_path) as cur:
                cur.execute(
                    "UPDATE bills SET last_nudged_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), bill["id"]),
                )
    return drafts


def mark_on_time(db_path: str, bill_id: int, on_time: bool) -> None:
    """Josh-executed, Banks-tracked (scorecard: 'bills on time [100%]')."""
    with cursor(db_path) as cur:
        cur.execute("UPDATE bills SET on_time = ? WHERE id = ?", (1 if on_time else 0, bill_id))


def bills_on_time_pct(db_path: str) -> float:
    with cursor(db_path) as cur:
        cur.execute("SELECT on_time FROM bills WHERE on_time IS NOT NULL")
        rows = [r["on_time"] for r in cur.fetchall()]
    if not rows:
        return 100.0
    return 100.0 * sum(rows) / len(rows)


# --- Subscription keep/kill memos -------------------------------------------


def subscriptions_due_for_review(db_path: str, days: int = 14) -> list[dict]:
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM bills WHERE is_subscription = 1 AND keep_kill_candidate = 1 "
            "AND due_date <= date('now', ?) ORDER BY due_date ASC",
            (f"+{days} day",),
        )
        return [dict(r) for r in cur.fetchall()]


def keep_kill_memo(subscription: dict, recommendation: str, reason: str) -> Draft:
    """recommendation: 'keep' | 'kill'. Banks recommends; Josh decides — no auto-cancel."""
    amount = (subscription.get("amount_cents") or 0) / 100
    return Draft(
        kind="subscription_memo",
        to="you",
        subject=f"Keep or kill — {subscription['name']} (${amount:,.0f}/{subscription['cadence']})",
        body=f"Recommendation: {recommendation}. Reason: {reason}. "
             f"Renews {subscription['due_date']} — Banks only recommends; you decide.",
    )


def record_memo(db_path: str, bill_id: int, memo_text: str) -> None:
    with cursor(db_path) as cur:
        cur.execute("UPDATE bills SET keep_kill_memo = ? WHERE id = ?", (memo_text, bill_id))
