"""Issues — 3 reds → Issue; every closed Issue names its permanent artifact (Phase I T2-7).

Auto-creation rules:
  - 3+ reds on a single scorecard week → open an Issue.
  - 3 consecutive red-week snapshots → open a 'streak' Issue.
Every closed Issue must supply `artifact` — what permanent thing was made.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .store import cursor
from .scorecard import count_reds, render_weekly_scorecard

REDS_THRESHOLD = 3
CONSECUTIVE_WEEKS_THRESHOLD = 3


def open_issue(db_path: str, title: str, trigger: str,
               week_ending: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO issues (title, trigger, week_ending, opened_at) "
            "VALUES (?, ?, ?, ?)",
            (title, trigger, week_ending, now),
        )
        return cur.lastrowid


def close_issue(db_path: str, issue_id: int, artifact: str) -> None:
    """Close an issue. `artifact` is required — what permanent thing was made."""
    if not artifact or not artifact.strip():
        raise ValueError("artifact is required to close an issue — what permanent thing was made?")
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE issues SET status = 'closed', artifact = ?, closed_at = ? WHERE id = ?",
            (artifact.strip(), now, issue_id),
        )


def open_issues(db_path: str) -> list[dict]:
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT * FROM issues WHERE status = 'open' ORDER BY opened_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def maybe_open_issue_for_week(db_path: str, week_ending: str) -> int | None:
    """If this week's scorecard has ≥3 reds, open an Issue. Returns issue_id or None."""
    lines = render_weekly_scorecard(db_path, week_ending)
    reds = count_reds(lines)
    if reds < REDS_THRESHOLD:
        return None
    # Don't duplicate — one Issue per week.
    with cursor(db_path) as cur:
        existing = cur.execute(
            "SELECT id FROM issues WHERE week_ending = ? AND trigger = '3_reds'",
            (week_ending,),
        ).fetchone()
    if existing:
        return existing["id"]
    title = f"{reds} reds on scorecard week {week_ending}"
    return open_issue(db_path, title, trigger="3_reds", week_ending=week_ending)


def maybe_open_streak_issue(db_path: str, recent_weeks: list[str]) -> int | None:
    """Open a streak Issue if the last N weeks were all red (≥3 reds each).

    `recent_weeks` should be the last CONSECUTIVE_WEEKS_THRESHOLD week_ending
    values in chronological order.
    """
    if len(recent_weeks) < CONSECUTIVE_WEEKS_THRESHOLD:
        return None
    for week in recent_weeks[-CONSECUTIVE_WEEKS_THRESHOLD:]:
        lines = render_weekly_scorecard(db_path, week)
        if count_reds(lines) < REDS_THRESHOLD:
            return None
    last_week = recent_weeks[-1]
    with cursor(db_path) as cur:
        existing = cur.execute(
            "SELECT id FROM issues WHERE week_ending = ? AND trigger = '3_consecutive_red_weeks'",
            (last_week,),
        ).fetchone()
    if existing:
        return existing["id"]
    title = (f"{CONSECUTIVE_WEEKS_THRESHOLD} consecutive red weeks ending {last_week} — "
             "sustained performance problem")
    return open_issue(db_path, title, trigger="3_consecutive_red_weeks",
                      week_ending=last_week)
