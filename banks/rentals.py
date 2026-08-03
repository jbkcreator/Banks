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
from .store import cursor

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


def relisting_draft(room_address: str, unit_label: str, rent_cents: int, platform: str) -> Draft:
    """Same-day re-listing draft. Platform + copy style are placeholders for Q15."""
    rent = rent_cents / 100
    return Draft(
        kind="relisting_sequence",
        to=f"(post to {platform})",
        subject=f"New listing draft — {unit_label} at {room_address}",
        body=(
            f"{unit_label} at {room_address} is available. ${rent:,.0f}/mo. "
            f"Draft prepared for {platform} — review and post when ready."
        ),
    )


# --- Inquiry pre-scoring -----------------------------------------------------


@dataclass(frozen=True)
class ApplicantCriteria:
    """Placeholder defaults — real standards come from Q18 (fair-housing-safe only)."""

    min_income_multiple: float = 3.0
    min_credit_score: int = 620


@dataclass(frozen=True)
class InquiryFacts:
    stated_income_cents: int | None
    credit_score: int | None
    move_in_fits_window: bool = True


def score_inquiry(facts: InquiryFacts, rent_cents: int, criteria: ApplicantCriteria) -> int:
    """0-100 score from legitimate, fair-housing-safe factors only.

    Never scores on protected classes — only income, credit, and timing,
    per the constitution's fair-housing caution (Q18).
    """
    score = 0
    if facts.stated_income_cents is not None and rent_cents:
        multiple = facts.stated_income_cents / rent_cents
        score += min(40, int(40 * multiple / criteria.min_income_multiple))
    if facts.credit_score is not None:
        score += min(40, int(40 * facts.credit_score / max(criteria.min_credit_score, 1)))
    if facts.move_in_fits_window:
        score += 20
    return max(0, min(100, score))


def inquiry_reply_draft(prospect_contact: str, unit_label: str, application_link: str) -> Draft:
    """Same-hour reply driving to the application link (link is Q17's answer)."""
    return Draft(
        kind="inquiry_reply",
        to=prospect_contact,
        subject=f"Re: your inquiry about {unit_label}",
        body=(
            f"Thanks for your interest in {unit_label}! You can apply here: "
            f"{application_link}"
        ),
    )


def record_inquiry_score(db_path: str, inquiry_id: int, score: int) -> None:
    with cursor(db_path) as cur:
        cur.execute("UPDATE inquiries SET score = ? WHERE id = ?", (score, inquiry_id))


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
