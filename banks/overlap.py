"""FA-name overlap flagging (Phase I T3-17).

Check draft recipients against a Josh-forwarded name list of FA contacts.
Flag to Josh — never coordinate across the wall. Banks does not know who the
FA people are until Josh forwards the list; until then the check is a no-op.

The name list lives at BANKS_FA_NAME_LIST_PATH (a plain-text file, one name
per line). Banks reads it on each check — no caching, so the file can be
updated without a restart.

Hard wall invariant: if an overlap is detected, Banks surfaces a flag draft
and STOPS. It never contacts that person, never passes the info across, never
coordinates. The flag itself is INTERNAL (no outbound send).
"""

from __future__ import annotations

import os
from pathlib import Path


def load_fa_names(path: str | None = None) -> frozenset[str]:
    """Load the FA name list. Returns empty set if the file doesn't exist yet."""
    p = path or os.environ.get("BANKS_FA_NAME_LIST_PATH")
    if not p or not Path(p).exists():
        return frozenset()
    lines = Path(p).read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip())


def check_fa_overlap(recipient: str, fa_names: frozenset[str] | None = None) -> bool:
    """True if the recipient name/email matches a known FA contact."""
    if fa_names is None:
        fa_names = load_fa_names()
    if not fa_names:
        return False
    lowered = recipient.strip().lower()
    # Check exact match or substring (name appears in email address).
    return lowered in fa_names or any(name in lowered for name in fa_names)


def flag_overlap_draft(recipient: str) -> "object":
    """Return a Draft flagging an FA-name overlap to Josh. INTERNAL only."""
    from .enforcement import Draft, sign
    return Draft(
        kind="fa_overlap_flag",
        to="you",
        subject=f"FA-name overlap detected — {recipient}",
        body=sign(
            f"Banks detected that '{recipient}' may be a Forced Action contact.\n\n"
            "This draft has been blocked. Banks does not coordinate across the wall.\n"
            "Please confirm whether to proceed or remove this recipient."
        ),
    )


def check_and_flag(db_path: str, recipient: str, chat,
                   fa_names: frozenset[str] | None = None) -> bool:
    """If overlap detected, surface a flag draft and return True (caller must stop).

    Returns False if no overlap — caller may proceed normally.
    """
    if not check_fa_overlap(recipient, fa_names):
        return False
    from .flow import propose
    from .packets import DecisionPacket
    from .refs import SendChannel
    draft = flag_overlap_draft(recipient)
    packet = DecisionPacket(
        kind="fa_overlap_flag",
        decision=f"FA-name overlap — proceed with draft to '{recipient}'?",
        recommendation="Do NOT proceed — remove recipient and re-draft",
        default_if_unanswered="block",
        evidence=f"recipient={recipient}",
    )
    propose(db_path, packet, draft, chat, send_channel=SendChannel.INTERNAL)
    return True
