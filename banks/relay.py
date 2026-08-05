"""Relay — deterministic executor that sends what Josh approved (A-D9/R-D1..R-D4).

The agent writes a send_intent (frozen payload, R-D2) and, on Approve, flips it
to 'approved'. Relay — run as its own step, the ONLY holder of the outbound
credential (mailer) — reads approved intents and sends exactly those bytes,
never re-reading the draft. Idempotent per draft_ref via sent_receipts UNIQUE:
a duplicate Approve can't double-send. Manual "Mark sent" suppresses the intent.

Not an agent. No LLM, no inference. Given an approved intent, it transmits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .mailer import Mailer
from .store import cursor

# send_channel → the From address to use. Test uses Resend's sandbox sender;
# real values arrive with the domain (banks@<domain>).
DEFAULT_FROM = "onboarding@resend.dev"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_intent(db_path: str, draft_ref: str, send_channel: str,
                   to_addr: str | None, subject: str | None, body: str | None) -> None:
    """Agent writes the frozen payload when a draft is proposed (status pending)."""
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO send_intents "
            "(draft_ref, send_channel, to_addr, subject, body, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (draft_ref, send_channel, to_addr, subject, body, _now()),
        )


def intent_channel(db_path: str, draft_ref: str) -> str | None:
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT send_channel FROM send_intents WHERE draft_ref=?", (draft_ref,)
        ).fetchone()
    return row["send_channel"] if row else None


def is_outbound(db_path: str, draft_ref: str) -> bool:
    ch = intent_channel(db_path, draft_ref)
    return bool(ch and ch.startswith("email:"))


def approve_intent(db_path: str, draft_ref: str) -> bool:
    """Approve flips a pending outbound intent to 'approved'. Returns enqueued?"""
    if not is_outbound(db_path, draft_ref):
        return False
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE send_intents SET status='approved' "
            "WHERE draft_ref=? AND status='pending'",
            (draft_ref,),
        )
    return True


def suppress_intent(db_path: str, draft_ref: str) -> None:
    """Manual 'Mark sent' — Josh sent it himself; Relay must never fire (R-D4)."""
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE send_intents SET status='suppressed' WHERE draft_ref=?",
            (draft_ref,),
        )


@dataclass(frozen=True)
class RelayResult:
    sent: list[str]
    skipped: list[str]
    failed: list[str]


def relay_run(db_path: str, mailer: Mailer, from_addr: str = DEFAULT_FROM) -> RelayResult:
    """Send every approved intent exactly once. Idempotent + receipted."""
    sent, skipped, failed = [], [], []
    with cursor(db_path) as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM send_intents WHERE status='approved'").fetchall()]

    for intent in rows:
        ref = intent["draft_ref"]
        # Claim: UNIQUE draft_ref means a second run/duplicate can't re-send.
        try:
            with cursor(db_path) as cur:
                cur.execute(
                    "INSERT INTO sent_receipts (draft_ref, status, updated_at) "
                    "VALUES (?, 'sending', ?)", (ref, _now()))
        except Exception:
            skipped.append(ref)  # already claimed/sent
            continue

        try:
            pid = mailer.send(from_addr, intent["to_addr"] or "",
                              intent["subject"] or "", intent["body"] or "")
            with cursor(db_path) as cur:
                cur.execute("UPDATE sent_receipts SET status='sent', provider_id=?, "
                            "updated_at=? WHERE draft_ref=?", (pid, _now(), ref))
                cur.execute("UPDATE send_intents SET status='sent' WHERE draft_ref=?",
                            (ref,))
            sent.append(ref)
        except Exception as exc:  # stays 'sending'/failed -> ages in the brief
            with cursor(db_path) as cur:
                cur.execute("UPDATE sent_receipts SET status='failed', error=?, "
                            "updated_at=? WHERE draft_ref=?", (str(exc), _now(), ref))
            failed.append(ref)

    return RelayResult(sent=sent, skipped=skipped, failed=failed)
