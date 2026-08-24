"""Job dispatch — turns a due StandingJob into a real posted action (#4).

scheduler.py is the clock (which jobs are due); this is the hands. A tick calls
run_due_jobs(now, db, chat): for each due job it performs the side effect —
morning_dashboard posts the B-D1 brief to #banks. Other jobs are wired as their
engines land. Keeps the clock pure and testable, the effects here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .activity_log import hours_saved_this_week, log_event
from .briefing import render_brief_blocks
from .chatport import ChatPort
from .halt import check_halt
from .schedule import OpportunityCostInputs, roi_line, weekly_roi
from .scheduler import due_jobs
from .scorecard import render_weekly_scorecard, count_reds


def _weekly_scorecard_blocks(db_path: str) -> list[dict]:
    from datetime import date, timedelta
    today = date.today()
    # Most recent Friday (or today if Friday).
    days_back = (today.weekday() - 4) % 7
    week_ending = (today - timedelta(days=days_back)).isoformat()
    lines = render_weekly_scorecard(db_path, week_ending)
    hrs = hours_saved_this_week(db_path)
    reds = count_reds(lines)
    emoji = "🔴" if reds >= 3 else ("🟡" if reds >= 1 else "🟢")
    header = f"{emoji} Weekly Scorecard — {week_ending}"
    body_lines = [
        f"{'🔴' if ln.red else '🟢'} {ln.label}: {ln.value} (target {ln.target})"
        for ln in lines
    ]
    roi = weekly_roi(OpportunityCostInputs(hours_saved=hrs))
    body_lines.append(f"⏱ {roi_line(roi)}")
    return [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(body_lines)},
        },
    ]


def run_job(name: str, db_path: str, chat: ChatPort) -> dict | None:
    """Perform one named job. Returns the post result, or None if no effect yet."""
    check_halt()  # T3-14: every job checks the halt flag before doing any work
    if name == "morning_dashboard":
        log_event(db_path, "draft_created", meta={"job": "morning_dashboard"}, minutes_saved=0)
        return chat.post_blocks("Banks — Morning Brief", render_brief_blocks(db_path))
    if name == "weekly_scorecard":
        log_event(db_path, "scorecard_posted", meta={"job": "weekly_scorecard"})
        return chat.post_blocks("Banks — Weekly Scorecard", _weekly_scorecard_blocks(db_path))
    if name == "nightly_reflection":
        from .reflection import run_reflection
        return run_reflection(db_path, chat)
    return None


def run_due_jobs(now: datetime, db_path: str, chat: ChatPort,
                 timezone_name: str = "America/New_York") -> list[str]:
    """Fire every job due at `now`. Returns the names actually run."""
    ran = []
    for job in due_jobs(now, timezone_name):
        if run_job(job.name, db_path, chat) is not None:
            ran.append(job.name)
    return ran
