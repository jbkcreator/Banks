"""Market-brief ingest + staleness clock (Q7).

Josh pastes a market brief into #banks daily. Banks stores it with a freshness
timestamp; if a day is missed the brief goes stale and Banks flags the context
as stale rather than reasoning from an outdated brief as if it were current
(the explicit Q7 requirement — degrade gracefully).

Storage rides on fact_freshness with kind 'market_brief' (1-day window).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .selfheal import FRESHNESS_DAYS, is_stale
from .store import cursor

BRIEF_FACT_KEY = "market_brief:latest"


def record_daily_brief(db_path: str, text: str, now: datetime | None = None) -> None:
    """Store the day's pasted market brief with a fresh timestamp."""
    ts = (now or datetime.now(timezone.utc)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO fact_freshness (fact_key, fact_kind, recorded_at, value)
            VALUES (?, 'market_brief', ?, ?)
            ON CONFLICT(fact_key) DO UPDATE SET
                recorded_at = excluded.recorded_at,
                value = excluded.value
            """,
            (BRIEF_FACT_KEY, ts, text),
        )


@dataclass(frozen=True)
class BriefStatus:
    present: bool          # any brief ever recorded
    stale: bool            # older than the 1-day window
    text: str | None       # the brief body, None if never recorded
    recorded_at: str | None


def brief_status(db_path: str, now: datetime | None = None) -> BriefStatus:
    now = now or datetime.now(timezone.utc)
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT recorded_at, value FROM fact_freshness WHERE fact_key = ?",
            (BRIEF_FACT_KEY,),
        )
        row = cur.fetchone()
    if row is None:
        return BriefStatus(present=False, stale=True, text=None, recorded_at=None)
    recorded_at = datetime.fromisoformat(row["recorded_at"])
    stale = is_stale("market_brief", recorded_at, now)
    return BriefStatus(
        present=True,
        stale=stale,
        text=None if stale else row["value"],
        recorded_at=row["recorded_at"],
    )


def brief_section_lines(db_path: str, now: datetime | None = None) -> list[str]:
    """Lines for the morning-brief 'Market brief' section (Q7 graceful degrade)."""
    status = brief_status(db_path, now)
    if not status.present:
        return ["_No market brief on file yet — paste one into #banks to begin._"]
    if status.stale:
        return [f"⚠️ _Brief is stale (last: {status.recorded_at[:10]}). "
                f"Flagging rather than reasoning from an outdated brief (Q7)._"]
    # Fresh — show first line/summary.
    first_line = (status.text or "").strip().splitlines()[0] if status.text else ""
    return [f"✓ Today's brief: {first_line[:200]}"]
