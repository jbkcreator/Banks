"""EmailPort: parse inbound confirmation emails and match to opportunities.

MOD-01 "forwarded email confirmation listener."
Josh forwards application confirmation emails to a dedicated Banks Gmail
(banks-intake@gmail.com). Banks polls that inbox via IMAP every 10 minutes
(scheduler job "email_intake_poll"), parses subjects for company names, and
records any new opportunities it finds.

Decisions (grilled 2026-08-28):
- Dedicated mailbox, not Josh's personal inbox — Banks only sees what Josh
  forwards; no OAuth to his personal Gmail needed.
- IMAP polling (imaplib, stdlib) — no third-party dependency.
- Poll cadence: every 10 minutes via scheduler.
- Credentials: BANKS_INTAKE_EMAIL + BANKS_INTAKE_EMAIL_PASSWORD (app password)
  in .env — same app-password pattern as SMTP.
"""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol


class EmailPort(Protocol):
    def get_confirmations(self) -> list[dict]: ...


class FakeEmailPort:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    def get_confirmations(self) -> list[dict]:
        return self._messages


class LiveImapEmailPort:
    """Polls banks-intake@gmail.com via IMAP for forwarded confirmation emails.

    Returns unread messages as dicts {subject, body, from, date} and marks
    them read so they are not returned on the next poll.

    Requires BANKS_INTAKE_EMAIL + BANKS_INTAKE_EMAIL_PASSWORD (Gmail app
    password) in config. Gmail IMAP must be enabled on the mailbox.
    """

    IMAP_HOST = "imap.gmail.com"

    def __init__(self, email_address: str, app_password: str) -> None:
        self._email = email_address
        self._password = app_password

    def get_confirmations(self) -> list[dict]:
        messages = []
        try:
            with imaplib.IMAP4_SSL(self.IMAP_HOST) as conn:
                conn.login(self._email, self._password)
                conn.select("INBOX")
                since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
                _, data = conn.search(None, f'(UNSEEN SINCE "{since}")')
                uids = data[0].split()
                print(f"[intake] poll start — {len(uids)} unread in last 24h")
                for uid in uids:
                    _, msg_data = conn.fetch(uid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    subject = msg.get("Subject", "")
                    from_addr = msg.get("From", "")
                    date = msg.get("Date", "")
                    body = _extract_body(msg)
                    # Cheap pre-narrow only (keyword). The REAL gate is downstream
                    # in intake: match against a company Josh actually applied to
                    # (from the Simplify CSV) + an LLM confirmation. We do NOT mark
                    # anything read — Josh's inbox is left completely untouched, and
                    # the 24h UNSEEN window + idempotency prevent reprocessing.
                    if is_confirmation_email(subject, body):
                        messages.append({
                            "subject": subject,
                            "body": body,
                            "from": from_addr,
                            "date": date,
                        })
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"[intake] ERROR — IMAP auth/network failure: {e}")
        return messages


def _extract_body(msg: email.message.Message) -> str:
    """Best-effort plain-text body extraction."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""


_CONFIRMATION_KEYWORDS = re.compile(
    r"application|applied|received your application|thank you for applying",
    re.IGNORECASE,
)

# A rejection or an interview/advance email also contains "application", but it
# is NOT a new-application confirmation — recording it as a fresh opportunity is
# noise (and a rejection could wrongly re-open a dead company). If any of these
# fire, it isn't intake's to record.
_NOT_CONFIRMATION = re.compile(
    r"unfortunately|not moving forward|will not be moving|decided (?:not |to )"
    r"|regret|no longer under consideration|position has been filled"
    r"|other candidates|schedule (?:your |a )?(?:call|interview)"
    r"|next steps|move forward with your|invite you to interview",
    re.IGNORECASE,
)


def is_confirmation_email(subject: str, body: str = "") -> bool:
    blob = f"{subject}\n{body}"
    if _NOT_CONFIRMATION.search(blob):
        return False  # rejection / interview-invite / advance — not a new app
    return bool(_CONFIRMATION_KEYWORDS.search(blob))


_STOP = r"(?=\s+(?:has been|was\b|is\b|received|for\b|–|\|)|\s+-\s|\s*$)"
_NAME = r"([A-Za-z0-9][A-Za-z0-9 &,.\-]*?)"
_NAME_PATTERNS = [
    r"application (?:to|at|for) " + _NAME + _STOP,
    r"applied (?:to|at|for) " + _NAME + _STOP,
    r"submitted to " + _NAME + _STOP,
    r"applying to " + _NAME + _STOP,
    r"(?:at|to) " + _NAME + _STOP,
]

# Domains that identify the applicant-tracking system / mail host, NOT the
# hiring company — so a sender/forwarded-From on one of these tells us nothing
# about who the role is with. Skip them and fall through.
_GENERIC_DOMAINS = frozenset({
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkday.com", "workday.com",
    "icims.com", "smartrecruiters.com", "workable.com", "jobvite.com",
    "successfactors.com", "taleo.net", "bamboohr.com", "breezy.hr", "gem.com",
    "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com",
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
    "icloud.com", "protonmail.com", "amazonses.com", "sendgrid.net",
    "mailgun.org", "notifications.com", "noreply.com",
})
_DOMAIN_RE = re.compile(r"[\w.\-]+@([\w\-]+(?:\.[\w\-]+)+)")


def _company_from_domain(addr: str) -> str:
    """Turn a sender/forwarded-From address into a company name, or '' if the
    domain is a generic ATS/mail host (which names no company)."""
    m = _DOMAIN_RE.search(addr or "")
    if not m:
        return ""
    domain = m.group(1).lower().strip(".")
    # registrable-ish domain: drop leading subdomains (careers., jobs., no-reply.)
    parts = domain.split(".")
    if len(parts) >= 2:
        domain = ".".join(parts[-2:])
    if domain in _GENERIC_DOMAINS:
        return ""
    label = domain.split(".")[0]
    if not label or label in ("mail", "email", "no-reply", "noreply", "notifications"):
        return ""
    return label.replace("-", " ").title()


def _match_name(text: str) -> str:
    for pattern in _NAME_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def extract_company_from_subject(subject: str) -> str:
    """Best-effort company name extraction from a confirmation subject line."""
    return _match_name(subject or "")


# A forwarded confirmation carries the original headers inside the body, e.g.
#   From: Acme Careers <careers@acme.com>
_FWD_FROM_RE = re.compile(r"^\s*From:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_FWD_SUBJECT_RE = re.compile(r"^\s*Subject:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def extract_company(subject: str, body: str = "", from_addr: str = "") -> str:
    """Company name from a confirmation, trying the most reliable source first.

    1. subject line phrasing ("...applying to Acme")
    2. the forwarded original Subject/From inside the body (Josh forwards, so the
       real sender + original subject live in the body, not the envelope)
    3. body phrasing anywhere
    4. sender domain (direct-to-intake case; skipped for ATS/mail hosts)
    Returns '' only when none of these yield a name — caller then holds it as
    Unknown rather than inventing one.
    """
    name = extract_company_from_subject(subject)
    if name:
        return name

    body = body or ""
    # 2a. forwarded original Subject line -> same phrasing patterns
    for m in _FWD_SUBJECT_RE.finditer(body):
        got = _match_name(m.group(1))
        if got:
            return got
    # 2b. forwarded original From line -> its domain
    for m in _FWD_FROM_RE.finditer(body):
        got = _company_from_domain(m.group(1))
        if got:
            return got
    # 3. any confirmation phrasing in the body text
    got = _match_name(body)
    if got:
        return got
    # 4. envelope sender domain (only useful when not forwarded)
    return _company_from_domain(from_addr)
