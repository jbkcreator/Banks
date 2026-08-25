"""Company exclusion list (MOD-06 foundation, used by MOD-01 intake).

Company-only: an excluded company is never surfaced as an opportunity, but a
former employee who has since moved elsewhere is still contactable — the check
is against the opportunity's company, never a contact's employment history.
Checked against the normalised company slug so "Rent Solutions", "rent
solutions" and "Rent Solutions, LLC" all resolve to the same exclusion.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .normalise import normalise_company
from .store import cursor


def add_company_exclusion(db_path: str, company: str, reason: str | None = None) -> None:
    """Add (or update) a company on the exclusion list. Idempotent by slug."""
    slug = normalise_company(company)
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO company_exclusions (company_normalized, reason, added_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(company_normalized) DO UPDATE SET reason = excluded.reason",
            (slug, reason, now),
        )


def is_company_excluded(db_path: str, company: str) -> bool:
    """True if the company (in any casing/suffix form) is on the exclusion list."""
    slug = normalise_company(company)
    if not slug:
        return False
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT 1 FROM company_exclusions WHERE company_normalized = ?", (slug,)
        ).fetchone()
    return row is not None


def list_exclusions(db_path: str) -> list[dict]:
    with cursor(db_path) as cur:
        return [dict(r) for r in cur.execute(
            "SELECT company_normalized, reason, added_at FROM company_exclusions "
            "ORDER BY company_normalized"
        ).fetchall()]
