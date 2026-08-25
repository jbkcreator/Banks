"""Warm-path join (MOD-01 ↔ MOD-02).

Given an opportunity's company, find the people Josh already knows there — the
core of the warm-intro lane. Matches the opportunity's normalised company against
the contact graph and ranks by how warm/useful the connection is:

  recruiter_registry > alumni_csv > linkedin_csv

so "who do I know at Second Nature" surfaces the warmest contact first. This is
what connects a scored Tier A role (MOD-01) to a real human to reach (MOD-02).
"""
from __future__ import annotations

import re

from .normalise import normalise_company
from .store import cursor

# Outreach WARMTH ordering — a recruiter/former colleague beats a cold
# connection; a bare manual entry is the weakest referral. NOTE: intentionally
# distinct from intake._SOURCE_PRIORITY (merge label priority), where `manual`
# ranks highest — different purpose, so they are not shared.
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


def find_referral_paths(db_path: str, company: str, industry: str | None = None,
                        limit: int = 3) -> list[dict]:
    """Warm-intro + referral paths for a company, warmest first (P2).

    Two path types, both from 1st-degree data (no fake 2nd-degree hops):
      * 'direct'    — someone Josh already knows AT the company (warm intro)
      * 'recruiter' — a recruiter whose vertical_fit matches the role's industry
                      (the "secondary referral avenue" when Josh knows no one there)
    """
    direct = find_warm_contacts(db_path, company, limit=limit)
    for c in direct:
        c["path"] = "direct"

    recruiters: list[dict] = []
    if industry:
        toks = [t for t in re.split(r"[/,&\s]+", industry.lower()) if len(t) > 2]
        with cursor(db_path) as cur:
            rows = [dict(r) for r in cur.execute(
                "SELECT id, name, company, email, linkedin_url, source, title, "
                "vertical_fit, position FROM contacts WHERE source = 'recruiter_registry'"
            ).fetchall()]
        for r in rows:
            vf = (r.get("vertical_fit") or "").lower()
            if any(t in vf for t in toks):
                r["path"] = "recruiter"
                recruiters.append(r)

    return direct + recruiters[:limit]


def attach_contact(db_path: str, opportunity_id: int, contact_id: int) -> None:
    """Link the resolved warm contact to the opportunity (populates contact_id)."""
    with cursor(db_path) as cur:
        cur.execute("UPDATE opportunities SET contact_id = ? WHERE id = ?",
                    (contact_id, opportunity_id))


def describe_contact(c: dict) -> str:
    """One-line human description for the Slack card."""
    role = c.get("title") or c.get("position") or ""
    label = {"recruiter_registry": "recruiter", "alumni_csv": "former colleague",
             "linkedin_csv": "connection",
             "clay_enrichment": "resolved contact"}.get(c.get("source", ""), "contact")
    bits = [c.get("name", "").strip()]
    if role:
        bits.append(f"({role})")
    line = " ".join(b for b in bits if b)
    # Recruiter surfaced as a referral avenue (not "at this company").
    if c.get("path") == "recruiter":
        vf = c.get("vertical_fit") or ""
        return f"{line} — recruiter (referral avenue{', ' + vf if vf else ''})"
    return f"{line} — your {label}"
