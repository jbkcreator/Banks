"""Follow-up cadence rules.

3 touches max: Day 3, Day 7, Day 14 after application date — per the signed
Maximum Distribution Build plan (MOD-04, lines 60/161). (An earlier draft used
5/12/21; corrected to match the contract 2026-08-25.)
Auto-stop on: Slack 'got a reply' button OR status flips to interviewing/closed.
"""
from __future__ import annotations

import datetime

FOLLOW_UP_DAYS = [3, 7, 14]
MAX_TOUCHES = 3


def next_follow_up_date(applied_date: str, touches_sent: int) -> str | None:
    """Return ISO date of next follow-up, or None if cadence is complete."""
    if touches_sent >= MAX_TOUCHES:
        return None
    base = datetime.date.fromisoformat(applied_date)
    delta = FOLLOW_UP_DAYS[touches_sent]
    return (base + datetime.timedelta(days=delta)).isoformat()


def cadence_complete(touches_sent: int, status: str) -> bool:
    """True if follow-up should stop."""
    return touches_sent >= MAX_TOUCHES or status in ("interviewing", "closed")
