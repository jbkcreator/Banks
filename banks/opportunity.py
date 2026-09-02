"""Opportunity engine (Part 5 job 5): scheduled sweeps, drafted-never-submitted
applications, follow-up ledger, interview briefs.

Scope (career/job opportunities, per the working assumption pending Q28),
match criteria, and sources (Q29) are all client-pending; this module builds
the mechanics that plug them in. The no-embellishment guard is load-bearing:
`draft_application()` refuses to reference any fact not present in the
supplied career-facts dict — Banks cannot invent, ever.
"""

from __future__ import annotations

import email
import email.policy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .enforcement import Draft
from .store import cursor

if TYPE_CHECKING:
    from .llmport import LLMPort

_POSTING_EXTRACT_SYSTEM = (
    "Extract job/opportunity details from this email. "
    "Return ONLY valid JSON with keys: "
    "title (str), company (str|null), role_type (str|null), "
    "location (str|null), salary_range (str|null), source (str|null), "
    "key_requirements (list[str])."
)

_GAP_FLAG_SYSTEM = (
    "You are comparing a job posting to a candidate's career facts. "
    "Identify ONLY real gaps — requirements in the posting not present in career facts. "
    "Return ONLY valid JSON with keys: gaps (list[str]), match_score (int 0-100)."
)


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


# Map career-facts.md section headers → CareerFacts fields. Best-effort: an empty
# or missing file yields CareerFacts() (same as before this was wired), but a
# filled resume now actually populates the facts the drafters + revisions use.
_FACTS_SECTIONS = {
    "identity": "identity",
    "experience": "experience",
    "skills": "skills",
    "education": "education",
    "education / credentials": "education",
    "ventures": "ventures",
    "ventures / holdings": "ventures",
    "what josh is looking for": "seeking",
    "what josh is looking for (opportunity criteria)": "seeking",
}
_STR_FIELDS = {"identity", "seeking"}


def load_career_facts(path: str = "banks/memory/career-facts.md") -> "CareerFacts":
    """Parse career-facts.md into CareerFacts. Empty/missing → CareerFacts().

    Lines under a `## Section` header become that field's value; HTML comment
    placeholders (`<!-- ... -->`) and blank lines are ignored. Multi-line
    sections become a tuple (or joined string for identity/seeking).
    """
    import os

    if not os.path.exists(path):
        return CareerFacts()
    try:
        raw = open(path, encoding="utf-8").read()
    except OSError:
        return CareerFacts()

    buckets: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("## "):
            key = s[3:].strip().lower()
            current = _FACTS_SECTIONS.get(key)
            continue
        if current is None:
            continue
        if not s or s.startswith("<!--") or s.startswith(">") or s.startswith("#"):
            continue
        buckets.setdefault(current, []).append(s.lstrip("-* ").strip())

    kwargs: dict = {}
    for field_name, vals in buckets.items():
        vals = [v for v in vals if v]
        if not vals:
            continue
        if field_name in _STR_FIELDS:
            kwargs[field_name] = " ".join(vals)
        else:
            kwargs[field_name] = tuple(vals)
    return CareerFacts(**kwargs)


@dataclass(frozen=True)
class OpportunityCriteria:
    """Placeholder defaults — real criteria/sources come from Q29."""

    role_types: tuple[str, ...] = ()
    min_comp_cents: int | None = None
    remote_ok: bool = True


def record_opportunity(
    db_path: str,
    title: str,
    source: str,
    match_score: int,
    *,
    tier: str = "C",
    pursuit_mode: str | None = None,
    company_normalized: str | None = None,
    source_url: str | None = None,
    contact_id: int | None = None,
    needs_enrichment: bool = False,
    industry: str | None = None,
    location: str | None = None,
    status: str = "sourced",
) -> int:
    """Insert an opportunity row. MOD-01 columns (tier, pursuit_mode,
    company_normalized, source_url, needs_enrichment) are keyword-only so
    pre-MOD-01 callers keep working; the intake pipeline passes them so dedup
    Pass 1 (source_url) works and half-blind rows are held back (Decision 4).
    """
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO opportunities "
            "(title, source, criteria_match_score, status, tier, pursuit_mode, "
            " company_normalized, source_url, contact_id, needs_enrichment, "
            " industry, location) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, source, match_score, status, tier, pursuit_mode,
             company_normalized, source_url, contact_id,
             1 if needs_enrichment else 0, industry, location),
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


# --- Forwarded-posting pipeline (#7) ------------------------------------------

@dataclass
class PostingAnalysis:
    extracted: dict          # raw LLM extract of the posting
    gaps: list[str]          # requirements not covered by career_facts
    match_score: int         # 0-100
    draft: Draft             # application draft (from draft_application)


def _email_body(raw_email: str) -> str:
    try:
        msg = email.message_from_string(raw_email, policy=email.policy.default)
        subject = msg.get("Subject", "")
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_content()
                    break
        else:
            body = msg.get_content()
        return f"Subject: {subject}\n\n{body}"[:4000]
    except Exception:
        return raw_email[:4000]


def process_forwarded_posting(
    raw_email: str,
    facts: CareerFacts,
    llm: "LLMPort",
    db_path: str | None = None,
) -> PostingAnalysis:
    """Forwarded job email → extract → gap-flag → draft (never submit).

    If db_path provided, records the opportunity row automatically.
    """
    text = _email_body(raw_email)

    # Step 1: extract posting details.
    extracted = llm.extract_json(_POSTING_EXTRACT_SYSTEM, text)
    title = extracted.get("title") or "Untitled opportunity"
    source = extracted.get("source") or extracted.get("company") or "forwarded email"

    # Step 2: gap-flag against career facts.
    facts_summary = (
        f"Experience: {'; '.join(facts.experience)}\n"
        f"Skills: {', '.join(facts.skills)}\n"
        f"Education: {'; '.join(facts.education)}"
    )
    gap_input = (
        f"Job posting requirements: {extracted.get('key_requirements', [])}\n\n"
        f"Candidate facts:\n{facts_summary}"
    )
    gap_result = llm.extract_json(_GAP_FLAG_SYSTEM, gap_input)
    gaps = gap_result.get("gaps") or []
    match_score = gap_result.get("match_score") or 50

    # Step 3: draft application (no-embellishment guard is inside draft_application).
    app_draft = draft_application(title, facts)

    # Append gap summary to draft body if gaps exist.
    if gaps:
        gap_note = "\n\nGaps flagged (for your awareness):\n" + "\n".join(f"  • {g}" for g in gaps)
        app_draft = Draft(
            kind=app_draft.kind,
            to=app_draft.to,
            subject=app_draft.subject,
            body=app_draft.body + gap_note,
        )

    if db_path:
        opp_id = record_opportunity(db_path, title, source, match_score)
        mark_application_drafted(db_path, opp_id)

    return PostingAnalysis(
        extracted=extracted,
        gaps=gaps,
        match_score=match_score,
        draft=app_draft,
    )
