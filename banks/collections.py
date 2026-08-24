"""Collections — per-room rent tracking and day-1 nudge drafts (Phase I A1).

Banks tracks expected vs received rent and drafts nudges to Praise on day 1
of lateness. Never a payment path — tracking and reminding only.

Feeds `collections_on_time_pct` so scorecard line 5 can go green.
Reads live rent status from PadSplit SourcePort once creds land;
works against seeded rows in tests today.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .enforcement import Draft
from .packets import DecisionPacket
from .refs import SendChannel
from .store import cursor


# ---------------------------------------------------------------------------
# Charge management

def record_charge(db_path: str, room_id: int, period_start: str, period_end: str,
                  amount_cents: int, due_date: str) -> int:
    """Insert a rent charge for a room/period. Returns the new charge id."""
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO rent_charges (room_id, period_start, period_end, "
            "amount_cents, due_date, status) VALUES (?, ?, ?, ?, ?, 'pending')",
            (room_id, period_start, period_end, amount_cents, due_date),
        )
        return cur.lastrowid


def record_payment(db_path: str, room_id: int, charge_id: int,
                   amount_cents: int, source: str = "padsplit",
                   paid_at: str | None = None) -> None:
    """Record a rent payment and flip the charge to paid."""
    paid_at = paid_at or datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO rent_payments (room_id, charge_id, paid_at, amount_cents, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (room_id, charge_id, paid_at, amount_cents, source),
        )
        cur.execute(
            "UPDATE rent_charges SET status = 'paid' WHERE id = ?", (charge_id,)
        )


def mark_late(db_path: str, charge_id: int) -> None:
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE rent_charges SET status = 'late' WHERE id = ? AND status = 'pending'",
            (charge_id,),
        )


# ---------------------------------------------------------------------------
# Weekly watch

def overdue_charges(db_path: str, as_of: str | None = None) -> list[dict]:
    """All pending charges whose due_date has passed — feeds the weekly watch."""
    as_of = as_of or datetime.now(timezone.utc).date().isoformat()
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT rc.*, r.property_address, r.unit_label, r.tenant_name "
            "FROM rent_charges rc JOIN rooms r ON r.id = rc.room_id "
            "WHERE rc.due_date < ? AND rc.status = 'pending' "
            "ORDER BY rc.due_date ASC",
            (as_of,),
        ).fetchall()
    return [dict(r) for r in rows]


def collections_on_time_pct(db_path: str, period_start: str, period_end: str) -> float | None:
    """Pct of charges paid on time in [period_start, period_end].

    Returns None if there are no charges (avoids false 100%).
    'On time' = paid before or on due_date; waived charges are excluded.
    """
    with cursor(db_path) as cur:
        total = cur.execute(
            "SELECT COUNT(*) AS n FROM rent_charges "
            "WHERE period_start >= ? AND period_end <= ? AND status != 'waived'",
            (period_start, period_end),
        ).fetchone()["n"]
        if total == 0:
            return None
        on_time = cur.execute(
            "SELECT COUNT(*) AS n FROM rent_charges rc "
            "LEFT JOIN rent_payments rp ON rp.charge_id = rc.id "
            "WHERE rc.period_start >= ? AND rc.period_end <= ? "
            "AND rc.status != 'waived' "
            "AND rp.paid_at IS NOT NULL AND rp.paid_at <= rc.due_date || 'T23:59:59'",
            (period_start, period_end),
        ).fetchone()["n"]
    return round(on_time / total * 100, 1)


# ---------------------------------------------------------------------------
# Day-1 nudge

def _rent_nudge_draft(tenant_name: str | None, unit_label: str,
                      property_address: str, amount_cents: int,
                      due_date: str) -> Draft:
    name = tenant_name or "Tenant"
    return Draft(
        kind="rent_nudge",
        to="praise@example.com",   # Praise handles collections (C-D1); real addr from creds
        subject=f"Rent nudge — {unit_label} at {property_address}",
        body=(
            f"Hi Praise,\n\n"
            f"{name} in {unit_label} at {property_address} has a rent charge of "
            f"${amount_cents/100:,.0f} due {due_date} that is now overdue (day 1).\n\n"
            f"Please follow up. — Banks."
        ),
    )


def surface_overdue_nudges(db_path: str, chat, as_of: str | None = None) -> list:
    """Draft day-1 nudges for every newly-overdue charge. Returns list of Proposed."""
    from .flow import propose  # deferred import: flow → chatport → approval → relay

    charges = overdue_charges(db_path, as_of)
    results = []
    for ch in charges:
        # Only nudge charges that just crossed the line (not already nudged).
        # Use the decision_packets table as the nudge record — if a nudge packet
        # already exists for this charge, skip it.
        ref_key = f"rent_nudge:{ch['id']}"
        with cursor(db_path) as cur:
            existing = cur.execute(
                "SELECT id FROM decision_packets WHERE kind = 'rent_nudge' AND evidence = ?",
                (ref_key,),
            ).fetchone()
        if existing:
            continue

        draft = _rent_nudge_draft(
            ch.get("tenant_name"), ch["unit_label"],
            ch["property_address"], ch["amount_cents"], ch["due_date"],
        )
        packet = DecisionPacket(
            kind="rent_nudge",
            decision=(f"Rent overdue — {ch['unit_label']} at {ch['property_address']} "
                      f"(${ch['amount_cents']/100:,.0f} due {ch['due_date']})"),
            recommendation="Send day-1 nudge to Praise",
            default_if_unanswered="send_nudge",
            evidence=ref_key,
            dollar_impact_cents=ch["amount_cents"],
        )
        result = propose(db_path, packet, draft, chat, send_channel=SendChannel.PRAISE)
        results.append(result)
        mark_late(db_path, ch["id"])
    return results
