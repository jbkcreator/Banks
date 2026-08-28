"""Exclusion wall (MOD-06). Three kinds, two gates.

Kinds:
- **Company** — an excluded company is never surfaced as an opportunity. The
  check is against the opportunity's company, never a contact's employment
  history, so a former employee who has moved on stays contactable.
- **Person** — keyed on a STABLE identity (LinkedIn URL first, normalised name
  fallback), so an excluded person stays blocked across job/email changes.
- **Indirect** — a company whose normalised name *contains* an excluded slug
  ("Rent Solutions Holdings/LLC/Group"), and any warm-intro conduit who works at
  an excluded firm (never launder outreach through someone there).

Normalisation makes "Rent Solutions", "rent solutions" and "Rent Solutions, LLC"
all resolve to the same slug. Both gates (draft-time in intake/surround,
send-time in relay) reuse these predicates.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .normalise import normalise_company, normalise_name
from .store import cursor


class DraftExcluded(RuntimeError):
    """Raised when a draft's target is on the exclusion wall. Carries the reason."""


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


# --- Indirect (through excluded firms) --------------------------------------

def is_indirectly_excluded(db_path: str, company: str | None) -> bool:
    """True if the company's slug CONTAINS an excluded slug.

    Catches corporate variants ("Rent Solutions Holdings/Group/LLC") without a
    parent/subsidiary data source. Deliberately shallow — anything beyond a name
    match (affiliates, portfolio companies) needs Josh's named list, not
    inference (client question #11).
    """
    slug = normalise_company(company or "")
    if not slug:
        return False
    with cursor(db_path) as cur:
        rows = cur.execute("SELECT company_normalized FROM company_exclusions").fetchall()
    return any(r["company_normalized"] and r["company_normalized"] in slug for r in rows)


def is_conduit_excluded(db_path: str, contact: dict) -> bool:
    """True if a warm-intro conduit works at an excluded (or indirectly excluded)
    firm — never route an introduction through someone there."""
    company = contact.get("company")
    if not company:
        return False
    return is_company_excluded(db_path, company) or is_indirectly_excluded(db_path, company)


# --- Person exclusion (stable identity: LinkedIn URL / normalised name) ------

def add_person_exclusion(
    db_path: str, *, linkedin_url: str | None = None, name: str | None = None,
    reason: str | None = None,
) -> None:
    """Exclude a named human. Needs at least one key (linkedin_url or name).

    Idempotent: the same (linkedin_url, name_normalized) pair isn't duplicated.
    """
    name_norm = normalise_name(name)
    if not linkedin_url and not name_norm:
        raise ValueError("add_person_exclusion needs a linkedin_url or a name")
    url = (linkedin_url or "").strip() or None
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        existing = cur.execute(
            "SELECT 1 FROM person_exclusions WHERE "
            "(? IS NOT NULL AND linkedin_url = ?) OR "
            "(? IS NOT NULL AND name_normalized = ?)",
            (url, url, name_norm, name_norm),
        ).fetchone()
        if existing:
            return
        cur.execute(
            "INSERT INTO person_exclusions (linkedin_url, name_normalized, reason, added_at) "
            "VALUES (?, ?, ?, ?)",
            (url, name_norm, reason, now),
        )


def is_person_excluded(
    db_path: str, *, linkedin_url: str | None = None, name: str | None = None,
) -> bool:
    """True if this human is excluded, matched on LinkedIn URL OR normalised name.

    Matching on the stable identity means an excluded person stays blocked after
    a job change (new company, new email) as long as the LinkedIn URL or name
    still matches.
    """
    url = (linkedin_url or "").strip() or None
    name_norm = normalise_name(name)
    if not url and not name_norm:
        return False
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT 1 FROM person_exclusions WHERE "
            "(? IS NOT NULL AND linkedin_url = ?) OR "
            "(? IS NOT NULL AND name_normalized = ?)",
            (url, url, name_norm, name_norm),
        ).fetchone()
    return row is not None


def is_contact_excluded(db_path: str, contact: dict) -> bool:
    """Person-exclusion check for a contact dict (name + linkedin_url)."""
    return is_person_excluded(
        db_path, linkedin_url=contact.get("linkedin_url"), name=contact.get("name")
    )


# --- The one gate: all kinds in a single predicate --------------------------

def is_target_excluded(
    db_path: str, *, company: str | None = None, contact: dict | None = None,
) -> tuple[bool, str | None]:
    """The single exclusion predicate — every kind, one place. Returns
    (excluded, reason). All three gates (intake early-skip, flow.propose
    every-draft chokepoint, relay send-time backstop) call THIS, so coverage is
    defined once and can't drift between stages.

    - company given → company + indirect (name-variant) check.
    - contact given → person (stable identity) + conduit (works-at-excluded-firm).
    """
    if company:
        if is_company_excluded(db_path, company):
            return True, f"excluded company: {company}"
        if is_indirectly_excluded(db_path, company):
            return True, f"indirectly excluded (variant of an excluded firm): {company}"
    if contact:
        if is_contact_excluded(db_path, contact):
            who = contact.get("name") or contact.get("linkedin_url") or "contact"
            return True, f"excluded person: {who}"
        if is_conduit_excluded(db_path, contact):
            return True, f"intro conduit at an excluded firm: {contact.get('company')}"
    return False, None


def list_person_exclusions(db_path: str) -> list[dict]:
    with cursor(db_path) as cur:
        return [dict(r) for r in cur.execute(
            "SELECT linkedin_url, name_normalized, reason, added_at "
            "FROM person_exclusions ORDER BY id"
        ).fetchall()]


# --- Seed file loader (source of truth) -------------------------------------

def load_exclusions_from_file(db_path: str, path: str) -> dict:
    """Load exclusions from a directive file. Idempotent (safe to re-run).

    Format (one per line, `#` comments and blanks ignored):
        company: Rent Solutions
        person: Jane Doe
        person: https://linkedin.com/in/jane

    A `person:` value starting with http(s) or containing 'linkedin.com' is
    treated as a LinkedIn URL; otherwise a name. Returns {"companies", "people"}.
    """
    import os
    counts = {"companies": 0, "people": 0}
    if not os.path.exists(path):
        return counts
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            kind, _, value = line.partition(":")
            kind = kind.strip().lower()
            value = value.strip()
            if not value:
                continue
            if kind == "company":
                add_company_exclusion(db_path, value)
                counts["companies"] += 1
            elif kind == "person":
                if value.lower().startswith(("http://", "https://")) or "linkedin.com" in value.lower():
                    add_person_exclusion(db_path, linkedin_url=value)
                else:
                    add_person_exclusion(db_path, name=value)
                counts["people"] += 1
    return counts
