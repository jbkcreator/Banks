"""Job dispatch — turns a due StandingJob into a real posted action (#4).

scheduler.py is the clock (which jobs are due); this is the hands. A tick calls
run_due_jobs(now, db, chat): for each due job it performs the side effect —
morning_dashboard posts the B-D1 brief to #banks. Other jobs are wired as their
engines land. Keeps the clock pure and testable, the effects here.
"""

from __future__ import annotations

from datetime import datetime

from .briefing import render_brief_blocks
from .chatport import ChatPort
from .scheduler import due_jobs


def run_job(name: str, db_path: str, chat: ChatPort) -> dict | None:
    """Perform one named job. Returns the post result, or None if no effect yet."""
    if name == "morning_dashboard":
        return chat.post_blocks("Banks — Morning Brief", render_brief_blocks(db_path))
    # nightly_reflection / weekly_scorecard / optimizers wire in as engines land.
    return None


def run_due_jobs(now: datetime, db_path: str, chat: ChatPort,
                 timezone_name: str = "America/New_York") -> list[str]:
    """Fire every job due at `now`. Returns the names actually run."""
    ran = []
    for job in due_jobs(now, timezone_name):
        if run_job(job.name, db_path, chat) is not None:
            ran.append(job.name)
    return ran
