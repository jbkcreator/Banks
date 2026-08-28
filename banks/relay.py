"""Relay — deterministic executor that sends what Josh approved (A-D9/R-D1..R-D4).

The agent writes a send_intent (frozen payload, R-D2) and, on Approve, flips it
to 'approved'. Relay — run as its own step, the ONLY holder of the outbound
credential (mailer) — reads approved intents and sends exactly those bytes,
never re-reading the draft. Idempotent per draft_ref via sent_receipts UNIQUE:
a duplicate Approve can't double-send. Manual "Mark sent" suppresses the intent.

Not an agent. No LLM, no inference. Given an approved intent, it transmits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .halt import check_halt
from .mailer import Mailer
from .refs import DraftRef, SendChannel
from .store import cursor

# send_channel → the From address to use. Test uses Resend's sandbox sender;
# real values arrive with the domain (banks@<domain>).
DEFAULT_FROM = "onboarding@resend.dev"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_INSERT_INTENT = (
    "INSERT OR REPLACE INTO send_intents "
    "(draft_ref, send_channel, to_addr, subject, body, status, created_at) "
    "VALUES (?, ?, ?, ?, ?, 'pending', ?)"
)


def enqueue_intent(db_path: str, draft_ref: DraftRef | str,
                   send_channel: SendChannel | str,
                   to_addr: str | None, subject: str | None, body: str | None,
                   cur=None) -> None:
    """Agent writes the frozen payload when a draft is proposed (status pending).

    Pass `cur` to join an existing `store.transaction()` so the intent commits
    together with its decision packet (candidate 5).
    """
    ref = DraftRef.parse(draft_ref)
    channel = SendChannel.parse(send_channel)
    params = (str(ref), channel.value, to_addr, subject, body, _now())
    if cur is not None:
        cur.execute(_INSERT_INTENT, params)
        return
    with cursor(db_path) as own:
        own.execute(_INSERT_INTENT, params)


def intent_channel(db_path: str, draft_ref: DraftRef | str) -> SendChannel | None:
    """The channel fixed at draft time, or None if there is no intent row."""
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT send_channel FROM send_intents WHERE draft_ref=?",
            (str(DraftRef.parse(draft_ref)),),
        ).fetchone()
    return SendChannel.parse(row["send_channel"]) if row else None


def is_outbound(db_path: str, draft_ref: DraftRef | str) -> bool:
    """Delegates to the channel's own answer — no prefix test lives here now."""
    channel = intent_channel(db_path, draft_ref)
    return channel.is_outbound if channel else False


def approve_intent(db_path: str, draft_ref: DraftRef | str) -> bool:
    """Approve flips a pending outbound intent to 'approved'. Returns enqueued?"""
    ref = DraftRef.parse(draft_ref)
    if not is_outbound(db_path, ref):
        return False
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE send_intents SET status='approved' "
            "WHERE draft_ref=? AND status='pending'",
            (str(ref),),
        )
    return True


def suppress_intent(db_path: str, draft_ref: DraftRef | str) -> None:
    """Manual 'Mark sent' — Josh sent it himself; Relay must never fire (R-D4)."""
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE send_intents SET status='suppressed' WHERE draft_ref=?",
            (str(DraftRef.parse(draft_ref)),),
        )


@dataclass(frozen=True)
class RelayResult:
    sent: list[str]
    skipped: list[str]
    failed: list[str]
    blocked: list[str] = field(default_factory=list)  # excluded at send time (MOD-06)


def _send_time_excluded(db_path: str, ref: str) -> bool:
    """MOD-06 send-time gate: re-check exclusion just before sending.

    Maps the intent's draft_ref → its outreach_lane → opportunity (company) and
    contact (person), and blocks if either became excluded after the draft was
    queued. Property-side / internal intents with no lane aren't job outreach,
    so they pass through untouched.
    """
    from .exclusion import is_target_excluded
    with cursor(db_path) as cur:
        lane = cur.execute(
            "SELECT opportunity_id, contact_id FROM outreach_lanes WHERE draft_ref = ?",
            (ref,),
        ).fetchone()
        if not lane:
            return False
        company = None
        if lane["opportunity_id"]:
            opp = cur.execute(
                "SELECT company_normalized FROM opportunities WHERE id = ?",
                (lane["opportunity_id"],),
            ).fetchone()
            company = opp["company_normalized"] if opp else None
        contact = None
        if lane["contact_id"]:
            contact = cur.execute(
                "SELECT name, linkedin_url FROM contacts WHERE id = ?",
                (lane["contact_id"],),
            ).fetchone()

    # One predicate — same coverage as intake + surround (draft-time). This is
    # the send-time backstop for the post-queue race (block at draft AND send).
    excluded, _reason = is_target_excluded(
        db_path, company=company, contact=dict(contact) if contact else None
    )
    return excluded


def _lookup_lane(db_path: str, draft_ref: str) -> dict | None:
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT id, contact_id, opportunity_id, lane_type "
            "FROM outreach_lanes WHERE draft_ref = ?",
            (draft_ref,),
        ).fetchone()
    return dict(row) if row else None


def relay_run(db_path: str, mailer: Mailer, from_addr: str = DEFAULT_FROM) -> RelayResult:
    """Send every approved intent exactly once. Idempotent + receipted.

    Order of gates per intent:
      1. Halt check (T3-14).
      2. MOD-06 send-time exclusion (re-check after queuing).
      3. Daily cap (channel-specific: email 40 / linkedin 20).
      4. 14-day per-contact spacing.
    After a successful send: mark_lane_sent, queue_cadence, record_funnel_event.
    """
    from datetime import date

    from .governance import (check_14day_spacing, check_and_increment,
                             mark_lane_sent, queue_cadence, record_funnel_event)

    check_halt()  # a halted Banks must never send — freeze, don't transmit.
    today = date.today().isoformat()
    sent, skipped, failed, blocked = [], [], [], []
    with cursor(db_path) as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM send_intents WHERE status='approved'").fetchall()]

    for intent in rows:
        ref = intent["draft_ref"]

        # Send-time gate (MOD-06 Q1): suppress anything excluded after queuing.
        if _send_time_excluded(db_path, ref):
            suppress_intent(db_path, ref)
            with cursor(db_path) as cur:
                cur.execute(
                    "INSERT OR REPLACE INTO sent_receipts "
                    "(draft_ref, status, error, updated_at) "
                    "VALUES (?, 'suppressed', 'excluded at send', ?)", (ref, _now()))
            blocked.append(ref)
            continue

        # Governance: daily cap check — keyed on lane_type (linkedin vs email).
        lane = _lookup_lane(db_path, ref)
        cap_key = "linkedin" if (lane and lane.get("lane_type") == "linkedin") else "email"
        if not check_and_increment(db_path, cap_key, today):
            blocked.append(ref)
            continue

        # Governance: 14-day per-contact spacing.
        if lane and lane.get("contact_id"):
            if not check_14day_spacing(db_path, lane["contact_id"], today):
                blocked.append(ref)
                continue

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
            if lane:
                mark_lane_sent(db_path, lane["id"])
                queue_cadence(db_path, lane["id"])
                record_funnel_event(db_path, lane["opportunity_id"], "outreach_sent")
            sent.append(ref)
        except Exception as exc:  # stays 'sending'/failed -> ages in the brief
            with cursor(db_path) as cur:
                cur.execute("UPDATE sent_receipts SET status='failed', error=?, "
                            "updated_at=? WHERE draft_ref=?", (str(exc), _now(), ref))
            failed.append(ref)

    return RelayResult(sent=sent, skipped=skipped, failed=failed, blocked=blocked)
