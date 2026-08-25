"""Warm-path join (MOD-01 ↔ MOD-02).

Given an opportunity's company, find the people Josh already knows there — the
core of the warm-intro lane. Matches the opportunity's normalised company against
the contact graph and ranks by how warm/useful the connection is:

  recruiter_registry > alumni_csv > linkedin_csv

so "who do I know at Second Nature" surfaces the warmest contact first. This is
what connects a scored Tier A role (MOD-01) to a real human to reach (MOD-02).
"""
from __future__ import annotations

from .normalise import normalise_company
from .store import cursor

# Warm-first ordering — a former colleague or a recruiter beats a cold connection.
_SOURCE_RANK = {"recruiter_registry": 3, "alumni_csv": 2, "linkedin_csv": 1, "manual": 0}

# Titles that signal a hiring decision-maker / functional owner (plan MOD-02).
_DECISION_MAKER = ("vp", "chief", "cro", "cmo", "ceo", "head of", "director",
                   "founder", "partner", "recruiter", "talent", "people")


def _relevance(row: dict) -> tuple[int, int]:
    """Sort key: (source rank, decision-maker signal). Higher = warmer/better."""
    text = f"{row.get('title') or ''} {row.get('position') or ''}".lower()
    dm = 1 if any(k in text for k in _DECISION_MAKER) else 0
    return (_SOURCE_RANK.get(row.get("source", ""), 0), dm)


def find_warm_contacts(db_path: str, company: str, limit: int = 3) -> list[dict]:
    """Return contacts at `company`, warmest first. Empty list if none known."""
    slug = normalise_company(company)
    if not slug:
        return []
    with cursor(db_path) as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT id, name, company, email, linkedin_url, source, title, "
            "vertical_fit, position FROM contacts WHERE company = ?", (slug,)
        ).fetchall()]
    return sorted(rows, key=_relevance, reverse=True)[:limit]


def attach_contact(db_path: str, opportunity_id: int, contact_id: int) -> None:
    """Link the resolved warm contact to the opportunity (populates contact_id)."""
    with cursor(db_path) as cur:
        cur.execute("UPDATE opportunities SET contact_id = ? WHERE id = ?",
                    (contact_id, opportunity_id))


def describe_contact(c: dict) -> str:
    """One-line human description for the Slack card."""
    role = c.get("title") or c.get("position") or ""
    label = {"recruiter_registry": "recruiter", "alumni_csv": "former colleague",
             "linkedin_csv": "connection"}.get(c.get("source", ""), "contact")
    bits = [c.get("name", "").strip()]
    if role:
        bits.append(f"({role})")
    line = " ".join(b for b in bits if b)
    return f"{line} — your {label}"
