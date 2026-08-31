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
                    if is_confirmation_email(subject, body):
                        print(f"[intake] confirmation: subject={subject!r} from={from_addr!r}")
                        messages.append({
                            "subject": subject,
                            "body": body,
                            "from": from_addr,
                            "date": date,
                        })
                        # Only mark read if it's a confirmation — leave other mail untouched
                        conn.store(uid, "+FLAGS", "\\Seen")
                    else:
                        print(f"[intake] skip (not confirmation): subject={subject!r}")
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


def is_confirmation_email(subject: str, body: str = "") -> bool:
    return bool(
        _CONFIRMATION_KEYWORDS.search(subject) or _CONFIRMATION_KEYWORDS.search(body)
    )


def extract_company_from_subject(subject: str) -> str:
    """Best-effort company name extraction from confirmation subject line."""
    # Stop at verb phrases, " - ", or end of string (space optional before EOL)
    _STOP = r"(?=\s+(?:has been|was\b|is\b|received|for\b|–|\|)|\s+-\s|\s*$)"
    _NAME = r"([A-Za-z0-9][A-Za-z0-9 &,.\-]*?)"
    patterns = [
        r"application (?:to|at|for) " + _NAME + _STOP,
        r"applied (?:to|at|for) " + _NAME + _STOP,
        r"submitted to " + _NAME + _STOP,
        r"applying to " + _NAME + _STOP,
        r"(?:at|to) " + _NAME + _STOP,
    ]
    for pattern in patterns:
        m = re.search(pattern, subject, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""
