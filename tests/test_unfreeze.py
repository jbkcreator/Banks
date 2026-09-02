"""Unfreezing a company. Until 2026-09-02 there was no way back once frozen —
"@banks resume" lifts the global kill switch, it never touched company_freeze.
hari froze Evolve, said "resume", and it stayed frozen with no code path to
reverse it short of editing the database by hand. This is the fix.
"""
from __future__ import annotations

import sqlite3

import pytest

from banks.commands import Command, handle_command, route
from banks.governance import freeze_company, is_company_frozen, unfreeze_company
from banks.halt import is_halt_command, is_unhalt_command
from banks.store import init_db


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "u.db")
    init_db(p)
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO opportunities (title, company_normalized, source, "
                  "status) VALUES (?,?,?,?)", ("AE", "evolve", "simplify", "applied"))
    return p


# --- governance layer -------------------------------------------------------

def test_unfreeze_removes_the_freeze_row(db):
    freeze_company(db, "evolve")
    assert is_company_frozen(db, "evolve")
    assert unfreeze_company(db, "evolve") is True
    assert not is_company_frozen(db, "evolve")


def test_unfreeze_a_company_that_was_never_frozen_is_a_safe_no_op(db):
    assert unfreeze_company(db, "evolve") is False


def test_unfreeze_reopens_frozen_cadence_rows(db):
    """A freeze also parks pending cadence touches as status='frozen'
    (governance.record_reply/got_reply). Deleting only the freeze row would
    leave those touches stuck forever — the company reads as unfrozen but
    nothing ever fires again."""
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO outreach_lanes (opportunity_id, lane_type, status, "
                  "created_at) VALUES (1, 'hiring_manager', 'sent', '2026-09-01T00:00:00Z')")
        lane_id = c.execute("SELECT id FROM outreach_lanes").fetchone()[0]
        c.execute("INSERT INTO cadence_queue (outreach_lane_id, touch_number, "
                  "due_date, status) VALUES (?, 1, '2026-09-05', 'frozen')", (lane_id,))
    freeze_company(db, "evolve")
    unfreeze_company(db, "evolve")
    with sqlite3.connect(db) as c:
        status = c.execute("SELECT status FROM cadence_queue").fetchone()[0]
    assert status == "pending"


# --- command layer ------------------------------------------------------

def test_exact_phrasing_unfreezes_immediately(db):
    freeze_company(db, "evolve")
    reply = handle_command(db, Command("unfreeze_company", "evolve", source="keyword"))
    assert "Resumed" in reply and "evolve" in reply
    assert not is_company_frozen(db, "evolve")


def test_unfreezing_something_not_frozen_says_so_honestly(db):
    reply = handle_command(db, Command("unfreeze_company", "evolve", source="keyword"))
    assert "wasn't frozen" in reply
    assert not is_company_frozen(db, "evolve")


def test_typo_is_resolved_not_rejected(db):
    freeze_company(db, "evolve")
    reply = handle_command(db, Command("unfreeze_company", "evolv", source="keyword"))
    assert "evolve" in reply.lower()


def test_no_company_named_asks(db):
    reply = handle_command(db, Command("unfreeze_company", None, source="keyword"))
    assert "which company" in reply.lower()


# --- router: regex fast-path ----------------------------------------------

def test_router_catches_resume_chasing_phrasing(db):
    cmd = route(db, "resume chasing Evolve", None)
    assert cmd.intent == "unfreeze_company" and cmd.company == "evolve"


def test_router_catches_bare_resume_company(db):
    cmd = route(db, "resume Evolve", None)
    assert cmd.intent == "unfreeze_company"


def test_router_catches_unfreeze_phrasing(db):
    cmd = route(db, "unfreeze Evolve", None)
    assert cmd.intent == "unfreeze_company" and cmd.company == "evolve"


# --- the collision this exposed: global resume must not swallow it --------

def test_bare_resume_is_still_a_global_unhalt():
    assert is_unhalt_command("resume") is True
    assert is_unhalt_command("resume all") is True
    assert is_unhalt_command("please resume") is True


def test_resume_with_a_company_is_not_a_global_unhalt():
    """Regression: `any(x in _UNHALT_TOKENS for x in words)` matched 'resume'
    appearing ANYWHERE in the message, so 'resume chasing Acme' triggered the
    GLOBAL unhalt (a silent no-op if not halted) before commands.py's
    unfreeze_company regex ever got a chance to see it."""
    for phrase in ["resume chasing Acme", "resume Acme",
                   "restart chasing Acme", "unhalt Acme"]:
        assert is_unhalt_command(phrase) is False, phrase


def test_global_halt_still_unaffected_by_this_change():
    """Sanity: is_halt_command's existing behavior must be untouched."""
    assert is_halt_command("stop all") is True
    assert is_halt_command("stop chasing Acme") is False
