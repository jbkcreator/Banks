"""Contact discipline — suppression list + 48h touch-log (Phase I T2-8).

Both checks are enforced inside flow.propose() via check_contact_discipline(),
so no draft can bypass them — one chokepoint, every surfacing.

Suppression: a permanent list of addresses/names Banks never contacts.
Touch-log: a 48h collision guard — if a draft was already sent to an address
in the last 48 hours, Banks must surface the collision rather than flood.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


class ContactSuppressed(Exception):
    """Raised when a draft recipient is on the permanent suppression list."""


class TouchCollision(Exception):
    """Raised when a draft recipient was already touched within 48 hours."""


TOUCH_WINDOW_HOURS = 336  # 14 days — MOD-04 follow-up cadence spans weeks, not hours


# ---------------------------------------------------------------------------
# Suppression list

def add_suppression(db_path: str, address: str, reason: str | None = None) -> None:
    from .store import cursor
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO suppression_list (address, reason, added_at) "
            "VALUES (?, ?, ?)",
            (address.lower().strip(), reason, now),
        )


def remove_suppression(db_path: str, address: str) -> None:
    from .store import cursor
    with cursor(db_path) as cur:
        cur.execute(
            "DELETE FROM suppression_list WHERE address = ?",
            (address.lower().strip(),),
        )


def is_suppressed(db_path: str, address: str) -> bool:
    from .store import cursor
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT 1 FROM suppression_list WHERE address = ?",
            (address.lower().strip(),),
        ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Touch log

def record_touch(db_path: str, address: str, draft_ref: str,
                 touched_at: str | None = None) -> None:
    from .store import cursor
    touched_at = touched_at or datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO touch_log (address, draft_ref, touched_at) VALUES (?, ?, ?)",
            (address.lower().strip(), draft_ref, touched_at),
        )


def last_touch(db_path: str, address: str) -> str | None:
    """ISO datetime of most recent touch for this address, or None."""
    from .store import cursor
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT touched_at FROM touch_log WHERE address = ? "
            "ORDER BY touched_at DESC LIMIT 1",
            (address.lower().strip(),),
        ).fetchone()
    return row["touched_at"] if row else None


def within_touch_window(db_path: str, address: str,
                        now: datetime | None = None) -> bool:
    """True if this address was touched within TOUCH_WINDOW_HOURS."""
    last = last_touch(db_path, address)
    if last is None:
        return False
    now = now or datetime.now(timezone.utc)
    then = datetime.fromisoformat(last)
    # Make both timezone-aware for comparison.
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then) < timedelta(hours=TOUCH_WINDOW_HOURS)


# ---------------------------------------------------------------------------
# Combined gate — called from flow.propose()

def check_contact_discipline(db_path: str, to_addr: str | None,
                              now: datetime | None = None) -> None:
    """Raise if the recipient is suppressed or was touched within 48h.

    Called from flow.propose() before any draft is persisted — one chokepoint,
    no draft bypasses this. Internal drafts (to_addr=None or empty) are skipped.
    """
    if not to_addr or not to_addr.strip():
        return  # internal-only drafts have no external recipient
    addr = to_addr.strip()
    if is_suppressed(db_path, addr):
        raise ContactSuppressed(
            f"Draft blocked: {addr!r} is on the permanent suppression list."
        )
    if within_touch_window(db_path, addr, now):
        raise TouchCollision(
            f"Draft blocked: {addr!r} was already contacted within "
            f"{TOUCH_WINDOW_HOURS}h. Surface the collision to Josh."
        )

# ---------------------------------------------------------------------------
# Email eligibility — the single answer to "can this contact be emailed?"
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def can_email(contact: dict | None) -> bool:
    """True when Banks holds a syntactically usable address for this contact.

    Gate policy (2026-09-02): presence of a real address, NOT the `verified`
    flag. Previously this required `verified AND email`, and since only the
    provider-enrichment path ever sets verified=1, every one of Josh's 1,694
    contacts routed to a LinkedIn DM — including the 17 who had a perfectly
    good address sitting in the column.

    Safety does not rest on this predicate: Josh approves every send, the MOD-06
    exclusion gate re-checks at send time, and the 40/day cap and 14-day spacing
    still apply. `verified` remains meaningful as provenance (a provider vouched
    for it) and is surfaced on the card, but it is not a routing veto.
    """
    if not contact:
        return False
    email = (contact.get("email") or "").strip()
    return bool(email) and bool(_EMAIL_RE.match(email))
