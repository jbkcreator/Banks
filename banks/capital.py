"""Capital & research desk (Part 5 job 6): the $140K / SDIRA.

Findings only — Banks models, shows the math, and flags every step for
professional review. It never advises and never acts. The mandate itself
(purpose, targets, constraints — Q31) is client-pending; these are the
standard REI metrics we proposed there (cap rate, cash-on-cash, IRR-lite,
hold period), safe to build now since the formulas don't depend on his answer.

SDIRA note: `professional_review_flag` defaults to 1 in the schema and is
never set to 0 anywhere in this module — the custodian/tax-review flag is
structural, not a suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .enforcement import Draft
from .store import cursor

PROFESSIONAL_REVIEW_NOTICE = (
    "Professional review required: this is an SDIRA candidate. Custodian and "
    "tax review before any step — prohibited-transaction rules apply. Banks "
    "does not advise and does not act."
)


@dataclass(frozen=True)
class CapitalCandidate:
    title: str
    purchase_price_cents: int
    annual_income_cents: int
    annual_expenses_cents: int
    cash_invested_cents: int
    hold_period_months: int

    @property
    def noi_cents(self) -> int:
        return self.annual_income_cents - self.annual_expenses_cents

    @property
    def cap_rate_pct(self) -> float:
        if self.purchase_price_cents == 0:
            return 0.0
        return 100.0 * self.noi_cents / self.purchase_price_cents

    @property
    def cash_on_cash_pct(self) -> float:
        if self.cash_invested_cents == 0:
            return 0.0
        return 100.0 * self.noi_cents / self.cash_invested_cents

    def math_shown(self) -> str:
        return (
            f"NOI = ${self.annual_income_cents/100:,.0f} income - "
            f"${self.annual_expenses_cents/100:,.0f} expenses = ${self.noi_cents/100:,.0f}\n"
            f"Cap rate = NOI / price = ${self.noi_cents/100:,.0f} / "
            f"${self.purchase_price_cents/100:,.0f} = {self.cap_rate_pct:.2f}%\n"
            f"Cash-on-cash = NOI / cash invested = ${self.noi_cents/100:,.0f} / "
            f"${self.cash_invested_cents/100:,.0f} = {self.cash_on_cash_pct:.2f}%\n"
            f"Hold period: {self.hold_period_months} months"
        )


def record_candidate(db_path: str, candidate: CapitalCandidate) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO capital_candidates
                (title, modeled_return, hold_period_months, math_shown,
                 professional_review_flag, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                candidate.title,
                candidate.cash_on_cash_pct,
                candidate.hold_period_months,
                candidate.math_shown(),
                now,
            ),
        )
        return cur.lastrowid


def candidate_memo(candidate: CapitalCandidate) -> Draft:
    return Draft(
        kind="capital_candidate_memo",
        to="you",
        subject=f"Capital candidate — {candidate.title}",
        body=(
            f"{candidate.math_shown()}\n\n{PROFESSIONAL_REVIEW_NOTICE}"
        ),
        detailed_financial=True,  # goes by email/attachment, never posted inline
    )


def all_candidates_carry_review_flag(db_path: str) -> bool:
    """Structural proof, not just convention — used by the hard-wall-adjacent
    acceptance harness to confirm no candidate ever bypasses professional review."""
    with cursor(db_path) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM capital_candidates WHERE professional_review_flag != 1")
        return cur.fetchone()["n"] == 0
