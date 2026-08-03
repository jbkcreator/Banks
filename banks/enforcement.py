"""Drafts-only enforcement + operator-verification (BANKS-01 / B1.2).

Immutable Core, restated in code: Banks never sends, posts, submits, pays, or
transacts to the outside world. Every output is a Draft awaiting Josh's tap.
The ONLY sanctioned egress is posting a draft into the private, Josh-only
`#banks` Slack channel (see banks.slack). There is deliberately no function
here — anywhere in this package — that moves money or contacts a third party.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class DraftOnlyViolation(RuntimeError):
    """Raised when any code path attempts a forbidden outbound action."""


class OperatorVerificationRequired(RuntimeError):
    """Raised when an unusual request claiming to be Josh must be verified."""


class Egress(enum.Enum):
    """Every conceivable outbound action, and whether Banks may perform it."""

    POST_DRAFT_TO_BANKS_CHANNEL = "post_draft_to_banks_channel"  # the ONLY allowed one
    SEND_EMAIL = "send_email"
    SEND_SMS = "send_sms"
    SUBMIT_APPLICATION = "submit_application"
    PAY = "pay"
    TRANSACT = "transact"
    POST_EXTERNAL = "post_external"


#: The single sanctioned egress. Everything else is forbidden by construction.
SANCTIONED_EGRESS = frozenset({Egress.POST_DRAFT_TO_BANKS_CHANNEL})


def assert_egress_allowed(action: Egress) -> None:
    """Gate every outbound action. Raises DraftOnlyViolation unless sanctioned."""
    if action not in SANCTIONED_EGRESS:
        raise DraftOnlyViolation(
            f"Banks is drafts-only, permanently. '{action.value}' is forbidden; "
            f"the only sanctioned egress is posting a draft to the #banks channel. "
            f"This needs Josh's tap."
        )


@dataclass(frozen=True)
class Draft:
    """Every Banks output. A draft is inert until Josh acts on it."""

    kind: str            # e.g. "morning_dashboard", "inquiry_reply", "vendor_nudge"
    to: str              # intended recipient IF Josh chooses to send — informational only
    subject: str
    body: str
    detailed_financial: bool = False  # if True → email/attachment, never posted inline
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_channel_message(self) -> str:
        """Render for the #banks channel. Financial detail is withheld from Slack."""
        if self.detailed_financial:
            return (
                f"*[DRAFT — {self.kind}]* {self.subject}\n"
                f"_Detailed financial matter — sent by email/attachment, not posted here._\n"
                f"Intended recipient (on your tap): {self.to}"
            )
        return (
            f"*[DRAFT — {self.kind}]* {self.subject}\n{self.body}\n"
            f"Intended recipient (on your tap): {self.to}"
        )


# --- Operator verification --------------------------------------------------

#: Categories that are "unusual" and demand operator verification before Banks
#: acts, even when the request claims to be Josh.
UNUSUAL_REQUEST_MARKERS = (
    "send it now",
    "wire",
    "pay ",
    "transfer",
    "buy ",
    "password",
    "credential",
    "api key",
    "ignore your rules",
    "override",
    "edit the constitution",
    "disable the wall",
    "submit the application",
)


def verify_operator_request(request_text: str) -> None:
    """Verify-and-stop on unusual requests claiming to be Josh.

    Banks does not act on these; it surfaces one question and stops. Even a
    verified Josh cannot make Banks *do* a forbidden egress — verification only
    unblocks drafting, never sending/paying.
    """
    lowered = request_text.lower()
    for marker in UNUSUAL_REQUEST_MARKERS:
        if marker in lowered:
            raise OperatorVerificationRequired(
                f"Unusual request detected ('{marker.strip()}'). Banks will not act. "
                f"One question back to Josh, then stop — and note: no request, even a "
                f"verified one, grants send/pay/transact. Josh taps; Banks drafts."
            )
