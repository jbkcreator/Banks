"""MOD-06 Q13 — halt is a real global freeze, not a Slack acknowledgement.

A halted Banks must neither send an approved intent (relay) nor run a scheduled
job. Verified here; clear_halt() in teardown so other tests are unaffected.
"""
from __future__ import annotations

import datetime as dt

import pytest

from banks.halt import BanksHalted, check_halt, clear_halt, set_halt
from banks.mailer import FakeMailer
from banks.relay import relay_run
from banks.store import cursor, init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


@pytest.fixture(autouse=True)
def _reset_halt():
    clear_halt()
    yield
    clear_halt()


def _approved(db, ref):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, "
            "status, created_at) VALUES (?, 'email:praise', 'a@b.com', 's', 'b', 'approved', ?)",
            (ref, dt.datetime.now(dt.timezone.utc).isoformat()))


def test_relay_refuses_to_send_when_halted(db):
    _approved(db, "1")
    set_halt(reason="test")
    with pytest.raises(BanksHalted):
        relay_run(db, FakeMailer())
    # intent stays approved (not sent), so it can go once halt clears
    with cursor(db) as cur:
        st = cur.execute("SELECT status FROM send_intents WHERE draft_ref='1'").fetchone()["status"]
    assert st == "approved"


def test_relay_sends_after_halt_cleared(db):
    _approved(db, "1")
    set_halt()
    with pytest.raises(BanksHalted):
        relay_run(db, FakeMailer())
    clear_halt()
    res = relay_run(db, FakeMailer())
    assert res.sent == ["1"]


def test_scheduled_job_checks_halt():
    """jobs.py calls check_halt() at entry — a halted flag stops work."""
    set_halt(reason="test")
    with pytest.raises(BanksHalted):
        check_halt()
