"""Read-only job-search inbox view (QA layer extension, approved 2026-09-02).

Josh granted Banks read access to his Gmail (BANKS_INTAKE_EMAIL). That access is
deliberately NARROW: this module is the only path from his mailbox to an answer,
and it drops every message that isn't demonstrably job-search related BEFORE
the content reaches an LLM prompt or a Slack channel.

Why the filter lives here and not in emailport: relevance is decided against the
DB (companies Josh applied to, contacts in his graph), and a port must not reach
into storage. `LiveImapEmailPort.fetch_recent()` bounds the fetch by date/size;
this module bounds it by *meaning*.

Non-negotiables (same contract as qa.py):
- Read-only. Nothing here writes to the DB, marks mail read, or sends anything.
- A message is surfaced only if `is_job_related()` returns True. Unrelated mail
  (personal, financial, medical) is discarded in-process and never leaves it.
- Bodies are truncated by the port and summarised to a one-line snippet here;
  full bodies are never returned.
- Callers must fence the output as untrusted — email is attacker-controlled text.
"""
from __future__ import annotations

import re
import time
from email.utils import parseaddr

from .normalise import normalise_company
from .store import cursor

# Applicant-tracking systems. Mail from these is about an application Josh
# actually submitted — an ATS does not cold-email you. Sufficient on its own.
_ATS_DOMAINS = frozenset({
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkday.com", "workday.com",
    "icims.com", "workable.com", "smartrecruiters.com", "jobvite.com",
    "taleo.net", "successfactors.com", "bamboohr.com", "breezy.hr",
    "simplify.jobs", "loopcv.pro",
})

# Job boards / marketplaces. NOT sufficient on their own: these blast job
# alerts and marketplace spam for roles Josh never applied to. Scope is
# applied jobs only, so mail from here must ALSO name a tracked company.
_JOB_BOARD_DOMAINS = frozenset({
    "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com",
    "upwork.com", "monster.com", "dice.com",
})

# Subject/body markers of a hiring conversation. Deliberately tight — a loose
# pattern here is what would leak unrelated mail into Slack.
_JOB_MARKERS = re.compile(
    r"\b(application|applied|applying|candidacy|candidate|recruit(?:er|ing|ment)?"
    r"|interview|hiring|job|role|position|opening|req(?:uisition)?"
    r"|resume|cv|offer letter|screening|phone screen|talent|onsite)\b",
    re.IGNORECASE,
)

_SNIPPET_CHARS = 220

# In-memory result cache. Deliberately NOT persisted: writing Josh's mail into
# banks.db would turn a transient read into stored personal data in an
# unencrypted file. This dies with the process, so a restart re-reads.
# 5 min keeps a Slack back-and-forth to one IMAP login while still catching a
# reply that lands mid-conversation.
CACHE_TTL_S = 300
_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    """Drop cached inbox reads (tests, and after a config change)."""
    _cache.clear()


def _sender_domain(from_header: str) -> str:
    """Bare domain of a From header ('A B <x@mail.acme.com>' -> 'mail.acme.com')."""
    _, addr = parseaddr(from_header or "")
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def _known_terms(db_path: str) -> tuple[set[str], set[str]]:
    """(company slugs, contact surnames) Banks already tracks — the match keys."""
    with cursor(db_path) as cur:
        companies = {
            r["company_normalized"] for r in
            cur.execute("SELECT DISTINCT company_normalized FROM opportunities")
            if r["company_normalized"]
        }
        contacts = {
            r["name"] for r in
            cur.execute("SELECT name FROM contacts WHERE name != ''")
        }
    surnames = set()
    for name in contacts:
        parts = [p for p in re.split(r"[\s,]+", name) if len(p) > 2]
        if parts:
            surname = parts[-1].lower()
            # Min length 4 + word-boundary matching below. A 3-letter surname
            # like "Mai" would otherwise match inside "e-MAIl@MAIl.variety.com"
            # and wave through every newsletter in the inbox (seen live
            # 2026-09-02: 'mai' alone passed 11 unrelated messages).
            if len(surname) >= 4 and surname.isalpha():
                surnames.add(surname)
    return companies, surnames


def _matches_tracked_company(domain: str, subject: str, companies: set[str],
                             blob: str = "") -> bool:
    """A company Josh APPLIED to is named in the sender domain or the subject,
    AND the message reads as being about a job.

    The job-language requirement matters because a tracked company can mail him
    about an unrelated business line: PadSplit is a tracked opportunity, and on
    2026-09-02 its marketplace sent "Message from Randy at 5207 Cillette Avenue"
    — same domain, nothing to do with his application.

    Body is included for the job-language check but NOT for the company match —
    a signature block naming a company would otherwise pull in unrelated threads.
    """
    domain_root = domain.split(".")[0] if domain else ""
    subject_slug = normalise_company(subject)
    for slug in companies:
        if not slug or len(slug) < 3:
            continue
        # Word-boundary match, not a bare substring test: "angi" (a real tracked
        # company) matched inside "ch-ANGI-ng" in an unrelated newsletter subject
        # and let it straight through the filter (found live 2026-09-02, same
        # failure mode as the surname substring bug fixed earlier that day).
        in_subject = bool(re.search(
            r"(?<![a-z0-9])" + re.escape(slug) + r"(?![a-z0-9])", subject_slug))
        if slug == domain_root or in_subject:
            return bool(_JOB_MARKERS.search(blob or subject))
    return False


def is_job_related(msg: dict, companies: set[str], surnames: set[str] = frozenset()) -> bool:
    """True only if this message concerns a job Josh actually APPLIED to.

    Scope narrowed 2026-09-02 on client instruction: applied jobs only, not the
    wider job search. Two grounds, either sufficient:
      1. Sender is an applicant-tracking system (greenhouse/lever/ashby/…) —
         an ATS only mails you about an application you submitted.
      2. Sender domain or subject names a company in `opportunities`.

    Deliberately NOT matched (all seen live in Josh's inbox 2026-09-02):
      - Job-board alerts for roles he never applied to (LinkedIn Job Alerts).
      - Marketplace traffic (Upwork messages).
      - Mail from a known contact that isn't tied to a tracked company — a
        recruiter's cold pitch is not an applied job.
    `surnames` is accepted for signature compatibility and intentionally unused.

    When in doubt, drop: a false negative costs an answer, a false positive
    leaks his private mail into Slack.
    """
    domain = _sender_domain(msg.get("from", ""))
    subject = msg.get("subject", "") or ""
    blob = f"{subject}\n{msg.get('body', '') or ''}"

    if any(domain == d or domain.endswith("." + d) for d in _JOB_BOARD_DOMAINS):
        # Job boards blast alerts — only surface if it names a company he applied to.
        return _matches_tracked_company(domain, subject, companies, blob)

    if any(domain == d or domain.endswith("." + d) for d in _ATS_DOMAINS):
        return True

    return _matches_tracked_company(domain, subject, companies, blob)


def _snippet(msg: dict) -> str:
    body = re.sub(r"\s+", " ", (msg.get("body") or "")).strip()
    return body[:_SNIPPET_CHARS] + ("…" if len(body) > _SNIPPET_CHARS else "")


def recent_job_mail(db_path: str, port, days: int = 14,
                    use_cache: bool = True) -> list[dict]:
    """Messages about applied-to jobs from the last `days`. Read-only.

    Returns dicts of {from, subject, date, snippet} — never full bodies.
    Cached in memory for CACHE_TTL_S so repeat questions in one Slack thread
    cost a single IMAP login.
    """
    key = (db_path, days)
    if use_cache:
        hit = _cache.get(key)
        if hit and (time.monotonic() - hit[0]) < CACHE_TTL_S:
            return hit[1]

    companies, surnames = _known_terms(db_path)
    out = []
    for msg in port.fetch_recent(days=days):
        if is_job_related(msg, companies, surnames):
            out.append({
                "from": msg.get("from", ""),
                "subject": msg.get("subject", ""),
                "date": msg.get("date", ""),
                "snippet": _snippet(msg),
            })
    if use_cache:
        _cache[key] = (time.monotonic(), out)
    return out


def format_job_mail(messages: list[dict], days: int = 14) -> str:
    """Plain-text rendering for a QA tool result."""
    if not messages:
        return (f"No email about your applications in the last {days} days. "
                f"(Banks only reads mail tied to a job you actually applied to — "
                f"not job alerts, and not the rest of your inbox.)")
    lines = [f"{len(messages)} email(s) about your applications, last {days} days:"]
    for m in messages:
        who = m["from"] or "unknown sender"
        lines.append(f"• {who} — {m['subject'] or '(no subject)'} [{m['date']}]")
        if m["snippet"]:
            lines.append(f"    {m['snippet']}")
    return "\n".join(lines)
