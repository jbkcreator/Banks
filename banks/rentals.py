"""Rental & property operations (Part 5 standing jobs 2, 3, 9).

Logic and templates only — built and testable against seeded rows now.
Real triggers (which signal counts as "vacant," which platforms to draft
for, applicant criteria, comp source) are placeholders pending client
answers (Q11/12/14/15/19/20); the shapes below are what plugs them in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .enforcement import Draft
from .packets import DecisionPacket
from .refs import SendChannel
from .store import cursor

# --- Surfacing helpers (architecture candidate 3) ----------------------------
#
# Six surfacings used to repeat the same ~25-line procedure: two function-local
# imports, a room fetch with an identical ValueError, a five-key DecisionPacket
# block, and a closing propose(). Only four things ever varied — the draft, the
# channel, and the copy. The duplication had already caused drift (mismatched
# signatures, an unused parameter), and Phase I's collections nudges and
# deadline radar would have become copies seven and eight.
#
# The shared tail now lives in _surface(); the room fetch in _room(). Each
# surfacing keeps its own signature, because those genuinely differ.


def _room(db_path: str, room_id: int):
    """Fetch the room a surfacing is about, or fail with a clear message."""
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT property_address, unit_label, current_rent_cents "
            "FROM rooms WHERE id = ?", (room_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"no room {room_id}")
    return row


def _surface(db_path: str, chat, *, draft: Draft, kind: str, decision: str,
             recommendation: str, default_if_unanswered: str,
             dollar_impact_cents: int = 0,
             channel: SendChannel = SendChannel.PRAISE):
    """Build the Decision Packet and hand the draft to the lifecycle.

    One chokepoint for every surfacing, so a rule added here (Phase I's
    suppression list and 48h touch-log check) applies to all of them.
    """
    from .flow import propose  # deferred: flow → chatport → approval → relay

    packet = DecisionPacket(
        kind=kind,
        decision=decision,
        recommendation=recommendation,
        default_if_unanswered=default_if_unanswered,
        dollar_impact_cents=dollar_impact_cents,
    )
    return propose(db_path, packet, draft, chat, send_channel=channel)


# --- Vacancy + days-vacant clock --------------------------------------------


def mark_vacant(db_path: str, room_id: int, signal_at: datetime | None = None) -> None:
    """Record a vacancy signal and start the days-vacant clock.

    The signal source itself (lease end date / manual flag / move-out email)
    is a client decision (Q11); this just records whichever one fired.
    """
    signal_at = signal_at or datetime.now(timezone.utc)
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE rooms SET occupied = 0, vacancy_signal_at = ?, days_vacant = 0, "
            "updated_at = ? WHERE id = ?",
            (signal_at.isoformat(), signal_at.isoformat(), room_id),
        )


def days_vacant(db_path: str, room_id: int, as_of: date | None = None) -> int | None:
    as_of = as_of or datetime.now(timezone.utc).date()
    with cursor(db_path) as cur:
        cur.execute("SELECT vacancy_signal_at FROM rooms WHERE id = ?", (room_id,))
        row = cur.fetchone()
    if row is None or row["vacancy_signal_at"] is None:
        return None
    signal_date = datetime.fromisoformat(row["vacancy_signal_at"]).date()
    return (as_of - signal_date).days


def surface_vacancy(db_path: str, room_id: int, chat, platform: str = "PadSplit"):
    """Turn a detected vacancy into a surfaced draft (#2: domain → lifecycle).

    Routed to Praise (C-D1 — she handles listing/syndication). Same-day listing
    is the biggest co-living revenue lever (Q9), so this fires as soon as a room
    goes vacant, priced at a month's rent as the at-risk figure.
    """
    row = _room(db_path, room_id)
    rent = row["current_rent_cents"] or 0
    days = days_vacant(db_path, room_id) or 0
    return _surface(
        db_path, chat,
        draft=relisting_draft(row["property_address"], row["unit_label"], rent, platform),
        kind="vacancy_relist",
        decision=f"{row['unit_label']} at {row['property_address']} is vacant "
                 f"({days}d) — list on {platform}?",
        recommendation=f"List at ${rent/100:,.0f}/mo today",
        default_if_unanswered="list at last rate",
        dollar_impact_cents=rent,
        channel=SendChannel.PRAISE,
    )


# Q10: listing platforms are an EXTENSIBLE set (PadSplit primary, Roomi, others —
# Praise holds the full list). Each platform gets a formatter; new ones register
# without touching relisting_draft. A formatter takes (address, unit, rent$) and
# returns the platform-specific body text.

def _generic_format(address: str, unit: str, rent: float) -> str:
    return (f"{unit} at {address} is available. ${rent:,.0f}/mo. "
            f"Review and post when ready.")


def _padsplit_format(address: str, unit: str, rent: float) -> str:
    return (f"Room available: {unit}, {address}. ${rent:,.0f}/mo, per-room co-living. "
            f"PadSplit syndicates onward once posted.")


def _roomi_format(address: str, unit: str, rent: float) -> str:
    return (f"Roommate wanted — {unit} at {address}. ${rent:,.0f}/mo, "
            f"move-in ready. Message to arrange a viewing.")


LISTING_FORMATTERS: dict[str, "callable"] = {
    "PadSplit": _padsplit_format,
    "Roomi": _roomi_format,
}


def register_listing_format(platform: str, formatter) -> None:
    """Add a new platform format at runtime (Q10 extensibility)."""
    LISTING_FORMATTERS[platform] = formatter


def relisting_draft(room_address: str, unit_label: str, rent_cents: int, platform: str) -> Draft:
    """Same-day re-listing draft, formatted per platform (Q10 extensible set)."""
    rent = rent_cents / 100
    formatter = LISTING_FORMATTERS.get(platform, _generic_format)
    return Draft(
        kind="relisting_sequence",
        to=f"(post to {platform})",
        subject=f"New listing draft — {unit_label} at {room_address}",
        body=formatter(room_address, unit_label, rent) + f"\n\n[Draft for {platform} — review before posting.]",
    )


# --- Inquiry handling (Q13: NO independent screening) ------------------------
#
# Q13 is explicit: Banks does NOT build independent applicant screening.
# PadSplit screens and presents a pool; Josh approves/declines from it. Banks'
# only role is to surface the platform-presented applicant for his decision, in
# PadSplit's own terms. The former income/credit scoring formula was removed —
# it was exactly the independent screening the client ruled out.


def inquiry_reply_draft(prospect_contact: str, unit_label: str, application_link: str) -> Draft:
    """Same-hour reply driving to PadSplit's own application flow (Q12)."""
    return Draft(
        kind="inquiry_reply",
        to=prospect_contact,
        subject=f"Re: your inquiry about {unit_label}",
        body=(
            f"Thanks for your interest in {unit_label}! You can apply here: "
            f"{application_link}"
        ),
    )


def surface_presented_applicant(
    db_path: str, room_id: int, applicant_name: str,
    padsplit_summary: str, chat,
) -> "object":
    """Surface a PadSplit-screened applicant for Josh's approve/decline (Q13).

    Banks does not score or rank — it relays PadSplit's own presentation and
    lets Josh decide. `padsplit_summary` is whatever the platform presented,
    verbatim; Banks adds no independent judgment.
    """
    row = _room(db_path, room_id)
    return _surface(
        db_path, chat,
        draft=Draft(
            kind="applicant_decision",
            to="you",
            subject=f"PadSplit applicant for {row['unit_label']} — your call",
            body=(
                f"PadSplit presented an applicant for {row['unit_label']} at "
                f"{row['property_address']}:\n\n{padsplit_summary}\n\n"
                f"Approve or decline in PadSplit — Banks relays only, never screens."
            ),
        ),
        kind="applicant_decision",
        decision=f"Applicant {applicant_name} for {row['unit_label']} — approve or decline?",
        recommendation="Your decision — Banks does not screen (Q13)",
        default_if_unanswered="no_action",
        dollar_impact_cents=row["current_rent_cents"] or 0,
        channel=SendChannel.INTERNAL,
    )


# --- Maintenance-to-closure state machine -----------------------------------

MAINTENANCE_STATES = ("open", "vendor_drafted", "closed")


def advance_maintenance(db_path: str, ticket_id: int, to_state: str, vendor_name: str | None = None) -> None:
    if to_state not in MAINTENANCE_STATES:
        raise ValueError(f"unknown maintenance state: {to_state}")
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        if to_state == "closed":
            cur.execute(
                "UPDATE maintenance_tickets SET status = ?, closed_at = ? WHERE id = ?",
                (to_state, now, ticket_id),
            )
        else:
            cur.execute(
                "UPDATE maintenance_tickets SET status = ?, vendor_name = COALESCE(?, vendor_name) "
                "WHERE id = ?",
                (to_state, vendor_name, ticket_id),
            )


def vendor_draft(vendor_name: str, room_address: str, description: str) -> Draft:
    return Draft(
        kind="vendor_dispatch",
        to=vendor_name,
        subject=f"Quote request — {room_address}",
        body=f"Requesting a quote/scheduling for: {description}",
    )


def open_maintenance_over(db_path: str, days: int = 7) -> list[dict]:
    """Feeds the scorecard's 'maintenance >7 days' line."""
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM maintenance_tickets WHERE status != 'closed' "
            "AND opened_at <= datetime('now', ?)",
            (f"-{days} day",),
        )
        return [dict(r) for r in cur.fetchall()]


# --- Turnover coordination ---------------------------------------------------

DEFAULT_TURNOVER_STEPS = (
    "move_out_inspection",
    "deep_clean",
    "repairs_touch_ups",
    "rekey_locks",
    "photos_for_listing",
    "relist_unit",
)


def turnover_draft(room_address: str, unit_label: str, step: str, owner: str) -> Draft:
    return Draft(
        kind="turnover_coordination",
        to=owner,
        subject=f"Turnover step — {step.replace('_', ' ')} — {unit_label}",
        body=f"{unit_label} at {room_address}: please handle '{step.replace('_', ' ')}'.",
    )


# --- Surfacing (#9): classify result → propose() ----------------------------

def surface_inquiry(db_path: str, room_id: int, prospect_contact: str,
                    application_link: str, chat) -> "object":
    """Surface a new inquiry for Josh's review via propose()."""
    from .flow import propose
    row = _room(db_path, room_id)
    return _surface(
        db_path, chat,
        draft=inquiry_reply_draft(prospect_contact, row["unit_label"], application_link),
        kind="inquiry_reply",
        decision=f"Inquiry for {row['unit_label']} from {prospect_contact} — reply with link?",
        recommendation="Send standard application link",
        default_if_unanswered="send_link",
        dollar_impact_cents=row["current_rent_cents"] or 0,
        channel=SendChannel.PRAISE,
    )


def surface_maintenance(db_path: str, ticket_id: int, vendor_name: str,
                        room_address: str, description: str, chat) -> "object":
    """Surface a maintenance vendor dispatch for Josh's approval."""
    return _surface(
        db_path, chat,
        draft=vendor_draft(vendor_name, room_address, description),
        kind="maintenance_dispatch",
        decision=f"Dispatch {vendor_name} for: {description[:80]}",
        recommendation=f"Send quote request to {vendor_name}",
        default_if_unanswered="contact vendor",
        channel=SendChannel.PRAISE,
    )


# Q16: review requests go through PadSplit's OWN review system (Google dropped).
# Trigger moments (the only ones that fire a request):
REVIEW_TRIGGERS = frozenset({
    "maintenance_resolved_promptly",
    "smooth_move_in",
    "unprompted_appreciation",
})
# Payment-streak trigger is configurable and OFF by default (Q16).
PAYMENT_STREAK_TRIGGER = "payment_streak"


def should_request_review(trigger: str, *, payment_streak_enabled: bool = False) -> bool:
    """Gate review requests to the client's approved trigger moments (Q16)."""
    if trigger in REVIEW_TRIGGERS:
        return True
    if trigger == PAYMENT_STREAK_TRIGGER:
        return payment_streak_enabled  # off by default
    return False


def surface_review_request(db_path: str, tenant_contact: str, room_address: str,
                           chat, trigger: str = "unprompted_appreciation",
                           payment_streak_enabled: bool = False) -> "object | None":
    """Surface a PadSplit review request — only on an approved trigger (Q16).

    Returns None (no draft) if the trigger isn't one Josh approved, so callers
    can wire in raw events without gating them first.
    """
    if not should_request_review(trigger, payment_streak_enabled=payment_streak_enabled):
        return None
    return _surface(
        db_path, chat,
        draft=Draft(
            kind="review_request",
            to=tenant_contact,
            subject="Quick favor — a PadSplit review?",
            body=(
                f"Hi, thanks for being a great tenant at {room_address}! "
                "If you have a moment, we'd really appreciate a review on PadSplit. "
                f"[Banks draft — triggered by: {trigger}. Review before sending.]"
            ),
        ),
        kind="review_request",
        decision=f"Send PadSplit review request to {tenant_contact}? (trigger: {trigger})",
        recommendation="Send — approved trigger moment",
        default_if_unanswered="send_review_request",
        channel=SendChannel.PRAISE,
    )


def surface_occasion(db_path: str, occasion: str, recipient: str, chat) -> "object":
    """Surface an occasion reminder (birthday/anniversary/deadline) draft."""
    return _surface(
        db_path, chat,
        draft=Draft(
            kind="occasion_reminder",
            to=recipient,
            subject=f"Upcoming: {occasion}",
            body=f"Banks flagged an upcoming occasion: {occasion} for {recipient}.",
        ),
        kind="occasion_reminder",
        decision=f"Flag occasion: {occasion} — send note to {recipient}?",
        recommendation="Send a brief note",
        default_if_unanswered="send_note",
        channel=SendChannel.INTERNAL,
    )


# --- Quarterly rate optimizer -------------------------------------------------


@dataclass(frozen=True)
class RateBenchmark:
    current_rent_cents: int
    comp_rent_cents: int  # from Q23's comp source, once wired

    @property
    def gap_cents(self) -> int:
        return self.comp_rent_cents - self.current_rent_cents

    @property
    def recommendation(self) -> str:
        return "raise" if self.gap_cents > 0 else "hold"


def rate_memo_draft(room_address: str, unit_label: str, benchmark: RateBenchmark) -> Draft:
    gap = benchmark.gap_cents / 100
    return Draft(
        kind="rate_optimizer_memo",
        to="you",
        subject=f"Quarterly rate review — {unit_label} at {room_address}",
        body=(
            f"Current rent ${benchmark.current_rent_cents/100:,.0f}/mo vs. comp "
            f"${benchmark.comp_rent_cents/100:,.0f}/mo. Recommendation: "
            f"{benchmark.recommendation} (${gap:+,.0f}/mo impact)."
        ),
    )
