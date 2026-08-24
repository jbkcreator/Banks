"""Activity log (B-D4) — append-only event journal.

Every Banks action that saves Josh time writes one row here. The ROI meter,
weekly scorecard, and nightly reflection all read from this table.
Never update rows — only insert.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .store import cursor


KIND_MINUTES: dict[str, float] = {
    "draft_created": 5.0,
    "draft_approved": 0.5,
    "draft_sent": 1.0,
    "vacancy_flagged": 10.0,
    "bill_nudged": 3.0,
    "opportunity_drafted": 30.0,
    "inquiry_answered": 5.0,
    "conflict_flagged": 8.0,
    "reflection_posted": 2.0,
    "receipt_filed": 4.0,
    "scorecard_posted": 3.0,
}


def log_event(
    db_path: str,
    kind: str,
    ref: str | None = None,
    meta: dict[str, Any] | None = None,
    minutes_saved: float | None = None,
    ts: datetime | None = None,
) -> int:
    """Append one event row. Returns new row id."""
    if minutes_saved is None:
        minutes_saved = KIND_MINUTES.get(kind, 0.0)
    now = (ts or datetime.now(timezone.utc)).isoformat()
    meta_s = json.dumps(meta) if meta else None
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO activity_log (kind, ref, minutes_saved, meta, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, ref, minutes_saved, meta_s, now),
        )
        return cur.lastrowid


def hours_saved_this_week(db_path: str, now: datetime | None = None) -> float:
    """Sum minutes_saved for events in the current ISO week, return as hours."""
    now = now or datetime.now(timezone.utc)
    # ISO week: Monday 00:00 UTC to now
    week_start = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - __import__("datetime").timedelta(days=now.weekday())
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT SUM(minutes_saved) AS total FROM activity_log WHERE ts >= ?",
            (week_start.isoformat(),),
        )
        row = cur.fetchone()
        total_minutes = row["total"] or 0.0
    return total_minutes / 60.0


def recent_events(db_path: str, limit: int = 20) -> list[dict]:
    """Fetch most recent activity events for reflection/recap."""
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT kind, ref, minutes_saved, meta, ts FROM activity_log "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
