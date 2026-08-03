"""Opportunity engine (Part 5 job 5): scheduled sweeps, drafted-never-submitted
applications, follow-up ledger, interview briefs.

Scope (career/job opportunities, per the working assumption pending Q28),
match criteria, and sources (Q29) are all client-pending; this module builds
the mechanics that plug them in. The no-embellishment guard is load-bearing:
`draft_application()` refuses to reference any fact not present in the
supplied career-facts dict — Banks cannot invent, ever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .enforcement import Draft
from .store import cursor


class UnknownCareerFact(RuntimeError):
    """Raised if a draft would reference a fact not present in career-facts."""


@dataclass(frozen=True)
class CareerFacts:
    """Loaded from banks/memory/career-facts.md content, structured.

    Only fields actually present here may appear in any application draft or
    interview brief — this is what "no embellishment, ever" means in code.
    """

    identity: str | None = None
    experience: tuple[str, ...] = field(default_factory=tuple)
    skills: tuple[str, ...] = field(default_factory=tuple)
    education: tuple[str, ...] = field(default_factory=tuple)
    ventures: tuple[str, ...] = field(default_factory=tuple)
    seeking: str | None = None

    def is_empty(self) -> bool:
        return not any([self.identity, self.experience, self.skills, self.education, self.ventures])


@dataclass(frozen=True)
class OpportunityCriteria:
    """Placeholder defaults — real criteria/sources come from Q29."""

    role_types: tuple[str, ...] = ()
    min_comp_cents: int | None = None
    remote_ok: bool = True


def record_opportunity(db_path: str, title: str, source: str, match_score: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO opportunities (title, source, criteria_match_score, status) "
            "VALUES (?, ?, ?, 'sourced')",
            (title, source, match_score),
        )
        return cur.lastrowid


def draft_application(opportunity_title: str, facts: CareerFacts) -> Draft:
    """Draft an application using ONLY what's in `facts`. Never submitted —
    Part 5: "queued (never submitted)". Enforced here by never writing a
    submit path, and at the schema layer (`opportunities.submitted` stays 0).
    """
    if facts.is_empty():
        raise UnknownCareerFact(
            "career-facts is empty — Banks cannot draft an application with no "
            "verified facts. Ask Josh to complete the career-facts file first."
        )
    body_lines = [f"Application draft for: {opportunity_title}", ""]
    if facts.identity:
        body_lines.append(facts.identity)
    if facts.experience:
        body_lines.append("Experience: " + "; ".join(facts.experience))
    if facts.skills:
        body_lines.append("Skills: " + ", ".join(facts.skills))
    if facts.education:
        body_lines.append("Education: " + "; ".join(facts.education))
    body_lines.append("")
    body_lines.append("[Drafted from career-facts only — never submitted without your review.]")
    return Draft(
        kind="opportunity_application",
        to="(queued — you submit)",
        subject=f"Draft application — {opportunity_title}",
        body="\n".join(body_lines),
    )


def mark_application_drafted(db_path: str, opportunity_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE opportunities SET application_drafted_at = ?, status = 'drafted' "
            "WHERE id = ?",
            (now, opportunity_id),
        )
        # submitted is never set here or anywhere — enforced by omission.


def interview_brief(opportunity_title: str, facts: CareerFacts) -> Draft:
    """Prep brief for whichever interview follows a drafted application —
    the counterparty depends on the opportunity type, resolved by Q28."""
    if facts.is_empty():
        raise UnknownCareerFact("cannot brief with no career-facts on file")
    matching = list(facts.experience) + list(facts.skills)
    return Draft(
        kind="interview_brief",
        to="you",
        subject=f"Interview brief — {opportunity_title}",
        body=(
            f"Likely relevant background to bring up: {'; '.join(matching) if matching else 'none on file'}.\n"
            f"What you're seeking: {facts.seeking or 'not specified in career-facts'}."
        ),
    )


def follow_up_ledger(db_path: str) -> list[dict]:
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM opportunities WHERE status = 'drafted' AND followed_up_at IS NULL "
            "ORDER BY application_drafted_at ASC"
        )
        return [dict(r) for r in cur.fetchall()]


def record_followup(db_path: str, opportunity_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE opportunities SET followed_up_at = ? WHERE id = ?", (now, opportunity_id)
        )
