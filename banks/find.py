"""Daily Find — one learning item per day (Phase I A3).

One curated item (article / fact / tip) or an honest 'none'. Feeds both the
morning brief and the scorecard's own line. Never fabricated — if no item was
recorded, brief shows the absence, not a placeholder.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass


VALID_KINDS = frozenset({"article", "fact", "tip", "none"})


@dataclass(frozen=True)
class DailyFind:
    date: str           # ISO date
    kind: str           # article | fact | tip | none
    title: str | None
    url: str | None
    summary: str | None


def record_find(db_path: str, kind: str, title: str | None = None,
                url: str | None = None, summary: str | None = None,
                date: str | None = None) -> None:
    """Record today's find (or a 'none' when nothing surfaced)."""
    from .store import cursor
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown find kind {kind!r} — expected one of {VALID_KINDS}")
    date = date or datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO daily_finds (date, kind, title, url, summary, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, kind, title, url, summary, now),
        )


def get_find(db_path: str, date: str | None = None) -> DailyFind | None:
    """Return the find for `date` (default today), or None if not yet recorded."""
    from .store import cursor
    date = date or datetime.now(timezone.utc).date().isoformat()
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT * FROM daily_finds WHERE date = ?", (date,)
        ).fetchone()
    if row is None:
        return None
    return DailyFind(
        date=row["date"], kind=row["kind"], title=row["title"],
        url=row["url"], summary=row["summary"],
    )


def find_brief_lines(db_path: str, date: str | None = None) -> list[str]:
    """One or two lines for the morning brief's Daily Find section."""
    find = get_find(db_path, date)
    if find is None:
        return ["No find recorded yet today."]
    if find.kind == "none":
        return ["— (nothing surfaced today)"]
    parts = [f"[{find.kind.upper()}] {find.title or '(no title)'}"]
    if find.summary:
        parts.append(find.summary)
    if find.url:
        parts.append(find.url)
    return parts
