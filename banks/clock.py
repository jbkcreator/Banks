"""Josh's day, not the server's.

The box runs UTC; Josh lives in America/New_York. A bare `date.today()` on the
server rolls over at 20:00 ET, which silently broke two user-facing guarantees
(found 2026-09-02):

  - Daily caps (40 email / 20 LinkedIn) keyed on the UTC date, so they reset at
    8pm ET. 40 sends at 7pm plus 40 at 8:01pm is 80 in one evening — exactly the
    "never look desperate" rule MOD-04 exists to enforce.
  - Snooze computed `date.today() + 1`, so an evening snooze cost two days.

Every "what day is it for Josh" decision must come through here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/New_York"


def _tz(timezone_name: str | None = None) -> ZoneInfo:
    if timezone_name:
        return ZoneInfo(timezone_name)
    try:                                  # avoid a hard config import at module load
        from .config import load_config
        return ZoneInfo(load_config().timezone or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def local_now(timezone_name: str | None = None) -> datetime:
    """Current time in Josh's timezone."""
    return datetime.now(timezone.utc).astimezone(_tz(timezone_name))


def today_local(timezone_name: str | None = None) -> date:
    """The date it is where Josh is — the unit every daily rule is keyed on."""
    return local_now(timezone_name).date()


def today_local_iso(timezone_name: str | None = None) -> str:
    return today_local(timezone_name).isoformat()


def local_date_plus(days: int, timezone_name: str | None = None) -> str:
    """ISO date `days` from Josh's today (snooze, cadence due dates)."""
    return (today_local(timezone_name) + timedelta(days=days)).isoformat()
