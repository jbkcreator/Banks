"""EmailPort: parse inbound confirmation emails and match to opportunities.

SPEC'D scope (MOD-01 "forwarded email confirmation listener"), intentionally
DORMANT: the parser/Fake exist but there is no LiveEmailPort or caller yet —
both blocked on the client mailbox/domain (CLIENT_QUERIES_V2 item 4b). Not
speculative — pending a client input.

Fake uses in-memory message dicts; Live would use IMAP/forwarding webhook.
Reply-stop is manual (Slack 'got a reply' button) — no inbox monitoring at launch.
"""
from __future__ import annotations

import re
from typing import Protocol


class EmailPort(Protocol):
    def get_confirmations(self) -> list[dict]: ...


class FakeEmailPort:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    def get_confirmations(self) -> list[dict]:
        return self._messages


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
    patterns = [
        r"application (?:to|at|for) ([A-Za-z0-9 &,.\-]+?) (?:for|–|-|\|)",
        r"(?:at|to) ([A-Za-z0-9 &,.\-]+?) –",
        r"your application(?: to)? ([A-Za-z0-9 &,.\-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, subject, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""
