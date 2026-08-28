"""MOD-03 lane drafters: one function per outreach lane type.

All drafts pull facts ONLY from CareerFacts — never invented.
Empty CareerFacts raises ValueError so the caller can surface the gap to Josh.
LLM is optional: when supplied it personalises copy; when absent a template fires.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .enforcement import Draft
from .opportunity import CareerFacts

if TYPE_CHECKING:
    from .llmport import LLMPort

_HIRING_MANAGER_SYSTEM = (
    "You are drafting a professional outreach email from a job applicant. "
    "Use ONLY the facts provided. Do not invent experience, titles, or numbers. "
    "Be concise (3-4 sentences). Return only the email body, no subject line."
)

_POV_BRIEF_SYSTEM = (
    "You are writing a brief (3-5 bullets) on why this candidate is a strong fit "
    "for this specific role. Use ONLY the provided career facts and job description. "
    "Label it 'draft POV — verify specifics'. No invented facts."
)


def _require_facts(facts: CareerFacts) -> None:
    if facts.is_empty():
        raise ValueError(
            "career-facts is empty — Banks cannot draft outreach without verified facts. "
            "Complete career-facts.md first."
        )


def _linkedin_action_line(contact: dict) -> str:
    """Human-Safe LinkedIn Handoff (MOD-03): DM compose deep-link or a flag if missing.

    Opens LinkedIn's compose window pre-addressed to the contact's profile.
    Josh clicks, pastes the draft above, hits Send — zero browser automation.
    Falls back to a plain profile URL if no messaging deep-link can be built.
    """
    url = (contact.get("linkedin_url") or "").strip()
    if not url:
        return "⚠️ No LinkedIn URL on file — find manually and paste draft above."
    # Extract profile slug from any linkedin.com/in/<slug>[/...] URL
    import re
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url)
    if m:
        slug = m.group(1).rstrip("/")
        dm_link = f"https://www.linkedin.com/messaging/thread/new/?recipient={slug}"
        return f"👉 Open LinkedIn DM: {dm_link}\n   (Paste draft above, then click 'Mark done')"
    # URL present but non-standard — link the profile directly
    return f"👉 LinkedIn profile: {url}\n   (Open, send DM manually, then click 'Mark done')"


def draft_hiring_manager_lane(
    title: str,
    company: str,
    contact: dict,
    facts: CareerFacts,
    llm: "LLMPort | None" = None,
) -> Draft:
    _require_facts(facts)
    name = contact.get("name") or "Hiring Manager"
    if llm:
        prompt = (
            f"Job: {title} at {company}\n"
            f"Recipient: {name} ({contact.get('title', 'unknown role')})\n"
            f"Candidate facts:\n"
            f"  Identity: {facts.identity or 'not specified'}\n"
            f"  Experience: {'; '.join(facts.experience)}\n"
            f"  Skills: {', '.join(facts.skills)}\n"
            f"  Seeking: {facts.seeking or 'not specified'}"
        )
        body = llm.complete(_HIRING_MANAGER_SYSTEM, prompt)
    else:
        exp = "; ".join(facts.experience[:2]) if facts.experience else "see resume"
        body = (
            f"Hi {name},\n\n"
            f"I came across the {title} role at {company} and wanted to reach out directly. "
            f"My background: {exp}. "
            f"{facts.seeking or 'I am actively exploring senior GTM opportunities.'}\n\n"
            f"Happy to share more if there's a fit.\n\n"
            f"[Draft from career-facts only — review before sending.]"
        )
    to = contact.get("email") or name
    return Draft(
        kind="hiring_manager_outreach",
        to=to,
        subject=f"Interest in {title} — {company}",
        body=body,
    )


def draft_recruiter_lane(title: str, company: str, facts: CareerFacts) -> Draft:
    _require_facts(facts)
    skills_blurb = (
        "; ".join(facts.skills[:2]) if facts.skills else "GTM leadership"
    )
    seeking = facts.seeking or f"my background in {skills_blurb}"
    body = (
        f"Hi,\n\n"
        f"I wanted to stay on your radar for GTM mandates — specifically roles like "
        f"{title} (or similar) where {seeking} could add value.\n\n"
        f"Happy to share my full background if you have relevant mandates.\n\n"
        f"[Draft from career-facts only — review before sending.]"
    )
    return Draft(
        kind="recruiter_outreach",
        to=company,
        subject="Keep me on file — GTM mandates",
        body=body,
    )


def draft_employee_lane(
    title: str, company: str, contact: dict, facts: CareerFacts
) -> Draft:
    _require_facts(facts)
    name = contact.get("name") or "there"
    has_email = bool(contact.get("verified") and contact.get("email"))
    action_line = "" if has_email else f"\n{_linkedin_action_line(contact)}"
    body = (
        f"Hi {name},\n\n"
        f"I noticed you're at {company} — I'm exploring the {title} opportunity there and "
        f"would love to hear about your experience at the company if you have a few minutes.\n\n"
        f"No pressure — happy to keep it brief.\n\n"
        f"[Draft from career-facts only — review before sending.]{action_line}"
    )
    to = contact.get("email") or contact.get("name") or "contact"
    return Draft(
        kind="employee_networking",
        to=to,
        subject=f"Question about {company}",
        body=body,
    )


def draft_warm_intro_ask(
    title: str, company: str, contact: dict, facts: CareerFacts
) -> Draft:
    _require_facts(facts)
    name = contact.get("name") or "there"
    background = facts.seeking or facts.identity or "(see resume)"
    body = (
        f"Hi {name},\n\n"
        f"I'm exploring the {title} role at {company} and noticed you might know someone there. "
        f"Would you be open to a quick introduction, or pointing me to the right person?\n\n"
        f"Background: {background}\n\n"
        f"Totally understand if it's not a fit — just thought I'd ask!\n\n"
        f"[Draft from career-facts only — review before sending.]"
    )
    to = contact.get("email") or contact.get("name") or "contact"
    return Draft(
        kind="warm_intro_ask",
        to=to,
        subject=f"Quick favor — intro to {company}?",
        body=body,
    )


def draft_linkedin_lane(
    title: str, company: str, contact: dict, facts: CareerFacts
) -> Draft:
    """LinkedIn connection note — copy-ready; Josh sends manually via 'Mark done'."""
    _require_facts(facts)
    name = contact.get("name") or "there"
    seeking = facts.seeking or "I am actively exploring senior GTM opportunities."
    action_line = _linkedin_action_line(contact)
    body = (
        f"Hi {name}, I came across the {title} role at {company} and wanted to connect. "
        f"{seeking} "
        f"Would love to connect if you're open to it.\n\n"
        f"[Draft from career-facts only — review before sending.]\n\n"
        f"{action_line}"
    )
    return Draft(
        kind="linkedin_outreach",
        to=contact.get("name") or "LinkedIn contact",
        subject=f"LinkedIn — {name} @ {company}",
        body=body,
    )


def draft_pov_brief(
    title: str,
    company: str,
    jd_summary: str,
    facts: CareerFacts,
    llm: "LLMPort | None" = None,
) -> Draft:
    """Tier A only — point-of-view brief on why Josh fits this specific role."""
    _require_facts(facts)
    if llm:
        prompt = (
            f"Job: {title} at {company}\n"
            f"JD summary: {jd_summary}\n"
            f"Career facts:\n"
            f"  Identity: {facts.identity or 'not specified'}\n"
            f"  Experience: {'; '.join(facts.experience)}\n"
            f"  Skills: {', '.join(facts.skills)}\n"
            f"  Ventures: {'; '.join(facts.ventures)}\n"
            f"  Seeking: {facts.seeking or 'not specified'}"
        )
        raw = llm.complete(_POV_BRIEF_SYSTEM, prompt)
        body = "draft POV — verify specifics\n\n" + raw
    else:
        highlights = list(facts.experience[:2]) + list(facts.skills[:2])
        body = (
            "draft POV — verify specifics\n\n"
            "Why this role fits:\n"
            + "\n".join(f"• {h}" for h in highlights)
            + "\n\n[Built from career-facts + JD. Verify specifics before using.]"
        )
    return Draft(
        kind="pov_brief",
        to="(internal review)",
        subject=f"POV brief — {title} @ {company}",
        body=body,
    )


def draft_consulting_lane(
    title: str, company: str, facts: CareerFacts,
    llm: "LLMPort | None" = None,
) -> Draft:
    """Fractional/consulting pitch — auto-routed when pursuit_mode is fractional/consulting."""
    _require_facts(facts)
    skills_blurb = "; ".join(facts.skills[:3]) if facts.skills else "GTM strategy"
    seeking = facts.seeking or f"fractional GTM leadership ({skills_blurb})"
    body = (
        f"Hi,\n\n"
        f"I've been following {company} and think there could be a fit for a fractional or "
        f"consulting engagement — particularly around the {title} scope.\n\n"
        f"My positioning: {seeking}\n"
        f"Relevant background: {'; '.join(facts.experience[:2]) if facts.experience else 'see resume'}\n\n"
        f"Happy to discuss scope, engagement shape, or timing — even informally.\n\n"
        f"[Draft from career-facts only — review before sending.]"
    )
    return Draft(
        kind="consulting_pitch",
        to=company,
        subject=f"Fractional/consulting interest — {company}",
        body=body,
    )
