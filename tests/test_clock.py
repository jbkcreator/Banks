"""Daily rules must key on Josh's day, not the server's.

The box is UTC; Josh is in Tampa (America/New_York). Between 20:00 ET and
midnight the UTC date is already tomorrow, which silently reset the daily send
caps mid-evening and made an evening Snooze last two days (found 2026-09-02).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from banks.clock import local_date_plus, today_local, today_local_iso


def test_evening_et_is_still_today_for_josh(monkeypatch):
    """21:00 ET on Sep 2 is 01:00 UTC Sep 3 — Josh's date must stay Sep 2."""
    import banks.clock as clock
    et_evening = datetime(2026, 9, 2, 21, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(clock, "local_now", lambda tz=None: et_evening)
    assert clock.today_local().isoformat() == "2026-09-02"
    # ...while the naive server call has already rolled over.
    assert et_evening.astimezone(timezone.utc).date().isoformat() == "2026-09-03"


def test_snooze_one_day_from_josh_evening_is_tomorrow_not_two_days(monkeypatch):
    import banks.clock as clock
    et_evening = datetime(2026, 9, 2, 21, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(clock, "local_now", lambda tz=None: et_evening)
    assert clock.local_date_plus(1) == "2026-09-03"


def test_explicit_timezone_overrides_config():
    assert today_local("UTC") == datetime.now(timezone.utc).date()


def test_unknown_timezone_falls_back_rather_than_crashing():
    """A bad BANKS_TIMEZONE must not take the scheduler down."""
    import banks.clock as clock
    assert isinstance(clock._tz(None), ZoneInfo)


def test_iso_helpers_agree():
    assert today_local_iso() == today_local().isoformat()
    assert local_date_plus(0) == today_local_iso()
    assert local_date_plus(3) == (today_local() + timedelta(days=3)).isoformat()
