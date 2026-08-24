"""Ramp-up mode — 30-day "ask freely" window (Phase I T3-16).

During the first 30 days Banks asks questions freely and captures answers
same-day to memory. After ramp-up, questions are batched to the brief
unless marked urgent. The window starts from BANKS_RAMPUP_START (ISO date
in .env). If unset, ramp-up is considered ended (conservative default).
"""

from __future__ import annotations

import os
from datetime import date, timedelta


RAMPUP_DAYS = 30


def rampup_start() -> date | None:
    raw = os.environ.get("BANKS_RAMPUP_START")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def in_rampup(as_of: date | None = None) -> bool:
    """True if we are still within the 30-day ramp-up window."""
    start = rampup_start()
    if start is None:
        return False
    as_of = as_of or date.today()
    return as_of < start + timedelta(days=RAMPUP_DAYS)


def rampup_days_remaining(as_of: date | None = None) -> int:
    """Days left in ramp-up, or 0 if ended."""
    start = rampup_start()
    if start is None:
        return 0
    as_of = as_of or date.today()
    end = start + timedelta(days=RAMPUP_DAYS)
    remaining = (end - as_of).days
    return max(0, remaining)


def should_batch_to_brief(urgent: bool = False, as_of: date | None = None) -> bool:
    """After ramp-up, non-urgent items are batched to the brief instead of surfaced immediately."""
    if in_rampup(as_of):
        return False   # ask freely during ramp-up
    return not urgent
