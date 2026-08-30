"""Scheduler runner loop — the clock that fires standing jobs each minute."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, time as dtime, timezone

import pytest

from banks.chatport import FakeChatPort
from banks.run import _run_one_tick
from banks.store import init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def test_tick_fires_due_job(db_path):
    # 07:30 ET -> morning_dashboard + daily_attack_queue are due.
    from zoneinfo import ZoneInfo
    now = datetime(2026, 8, 31, 7, 30, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    ran = _run_one_tick(db_path, FakeChatPort(), "America/New_York", now)
    assert "morning_dashboard" in ran


def test_tick_swallows_halt(db_path, monkeypatch):
    # A halted Banks must skip work but NOT crash the loop.
    from banks.halt import BanksHalted
    def boom(*a, **k):
        raise BanksHalted("stopped")
    monkeypatch.setattr("banks.jobs.run_due_jobs", boom)
    assert _run_one_tick(db_path, FakeChatPort(), "America/New_York") == []


def test_tick_swallows_error(db_path, monkeypatch):
    # One bad tick must never kill the clock.
    def boom(*a, **k):
        raise RuntimeError("bad tick")
    monkeypatch.setattr("banks.jobs.run_due_jobs", boom)
    assert _run_one_tick(db_path, FakeChatPort(), "America/New_York") == []


def test_quiet_minute_runs_nothing(db_path):
    # An off-schedule minute fires no jobs (and doesn't raise).
    now = datetime(2026, 8, 31, 3, 17, tzinfo=timezone.utc)
    assert _run_one_tick(db_path, FakeChatPort(), "America/New_York", now) == []
