"""Money truth: bills/renewals radar + subscription keep/kill memos (Part 5 job 4).

Track and remind ONLY — never pay, never transact. No function in this
module (or anywhere in Banks) moves money. The bill *source* (sheet, forwarded
emails, `#banks` message — Q25) is a client decision; this module works off
whatever lands in the `bills` table regardless of how it got there.

Bill pipeline (item #5): .eml/.txt → LLM extract → upsert bills table →
propose() confirm draft → 7+1 nudge. See extract_bill_from_email().
"""

from __future__ import annotations

import email
import email.policy
import json
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .enforcement import Draft
from .store import cursor

if TYPE_CHECKING:
    from .llmport import LLMPort

_BILL_EXTRACT_SYSTEM = """You extract bill/payment details from email text.
Return ONLY valid JSON with keys:
  name (str), amount_cents (int|null), due_date (str ISO-date|null),
  cadence (str: monthly|annual|one_time|quarterly|unknown),
  is_subscription (bool), property_address (str|null),
  bill_category (str: "property" if the bill is tied to a specific rental
    property/address, otherwise "personal").
If a field is not present in the email, use null."""


def extract_bill_from_email(raw_email: str, llm: "LLMPort") -> dict:
    """Parse raw .eml bytes/str → extracted bill fields via LLM."""
    try:
        msg = email.message_from_string(raw_email, policy=email.policy.default)
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_content()
                    break
        else:
            body = msg.get_content()
        subject = msg.get("Subject", "")
        text = f"Subject: {subject}\n\n{body}"[:3000]
    except Exception:
        text = raw_email[:3000]
    return llm.extract_json(_BILL_EXTRACT_SYSTEM, text)


def upsert_bill_from_extract(db_path: str, extracted: dict) -> int:
    """Insert or update a bill row from LLM-extracted fields. Returns bill id."""
    name = extracted.get("name") or "Unknown bill"
    due_date = extracted.get("due_date") or datetime.now(timezone.utc).date().isoformat()
    cadence = extracted.get("cadence") or "unknown"
    amount_cents = extracted.get("amount_cents")
    is_sub = 1 if extracted.get("is_subscription") else 0
    prop = extracted.get("property_address")
    # Q19: explicit tag; default from whether a property address is present.
    category = extracted.get("bill_category") or ("property" if prop else "personal")
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO bills (name, amount_cents, due_date, cadence, "
            "property_address, bill_category, is_subscription) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, amount_cents, due_date, cadence, prop, category, is_sub),
        )
        # Fetch the row (inserted or existing by name+due_date).
        cur.execute("SELECT id FROM bills WHERE name = ? AND due_date = ?", (name, due_date))
        row = cur.fetchone()
        return row["id"] if row else cur.lastrowid


def bill_confirm_draft(extracted: dict) -> Draft:
    """Confirm draft shown to Josh before a new bill is persisted."""
    name = extracted.get("name", "Unknown")
    amount = (extracted.get("amount_cents") or 0) / 100
    due = extracted.get("due_date", "unknown")
    cadence = extracted.get("cadence", "unknown")
    return Draft(
        kind="bill_confirm",
        to="you",
        subject=f"New bill detected — {name}",
        body=(
            f"Banks found a new bill in your email:\n\n"
            f"  Name: {name}\n"
            f"  Amount: ${amount:,.2f}\n"
            f"  Due: {due}\n"
            f"  Cadence: {cadence}\n\n"
            f"Approve to add to your bills tracker. Reject to discard."
        ),
    )


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


# --- Q19: personal vs property split + per-property expense rollup -----------


def expenses_by_property(db_path: str) -> list[dict]:
    """Roll property-level bills up per property (Q19 expense tracking).

    Personal bills are excluded — they roll up separately (see personal_total).
    """
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT COALESCE(property_address, '(untagged)') AS property_address, "
            "COUNT(*) AS bill_count, COALESCE(SUM(amount_cents), 0) AS total_cents "
            "FROM bills WHERE bill_category = 'property' "
            "GROUP BY property_address ORDER BY total_cents DESC"
        )
        return [dict(r) for r in cur.fetchall()]


def personal_expenses_total_cents(db_path: str) -> int:
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM bills "
            "WHERE bill_category = 'personal'"
        )
        return cur.fetchone()["total"]
