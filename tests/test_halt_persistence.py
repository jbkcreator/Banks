"""The kill switch must cross the process boundary and survive restart.

Banks runs as two services: banks-listener (hears "@banks stop all") and
banks-scheduler (runs relay_dispatch every 5 min — the process that actually
sends). Before 2026-09-02 the halt flag was a module global, so Josh's kill
switch set it in the listener while the scheduler kept sending. Slack said
"🛑 ALL jobs suspended" and outreach continued. These tests pin the fix.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from banks.halt import (BanksHalted, check_halt, clear_halt, halt_reason,
                        init_halt, is_halted, set_halt)
from banks.store import init_db

REPO = "/root/banks"


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "halt.db")
    init_db(path)
    init_halt(path)
    yield path
    init_halt(None)


def _in_subprocess(db_path: str, code: str) -> str:
    """Run code in a SEPARATE interpreter pointed at the same DB."""
    prog = f"from banks.halt import *\ninit_halt({db_path!r})\n{code}"
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                       text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_halt_set_in_one_process_is_seen_by_another(db):
    """THE regression: listener halts, scheduler must see it."""
    set_halt("operator command: 'stop all'")
    assert _in_subprocess(db, "print(is_halted())") == "True"


def test_scheduler_process_refuses_to_send_after_listener_halt(db):
    """check_halt() in a second process must raise — this is what stops Relay."""
    set_halt("stop all")
    out = _in_subprocess(db, (
        "try:\n"
        "    check_halt(); print('WOULD HAVE SENT')\n"
        "except BanksHalted:\n"
        "    print('BLOCKED')\n"))
    assert out == "BLOCKED"


def test_resume_in_one_process_is_seen_by_another(db):
    set_halt("stop all")
    clear_halt()
    assert _in_subprocess(db, "print(is_halted())") == "False"


def test_halt_survives_restart(db):
    """A deploy or crash must NOT silently resume outreach Josh stopped."""
    set_halt("stop all")
    init_halt(None)          # simulate process death
    init_halt(db)            # ...and restart against the same DB
    assert is_halted() is True


def test_reason_is_preserved_across_processes(db):
    set_halt("operator command: 'stop everything'")
    assert "stop everything" in _in_subprocess(db, "print(halt_reason())")
    assert "stop everything" in halt_reason()


def test_unreadable_state_fails_safe_to_halted(tmp_path):
    """If the flag can't be read we cannot prove sending is allowed — halt."""
    init_halt(str(tmp_path / "no_such_dir" / "x.db"))
    try:
        assert is_halted() is True
        with pytest.raises(BanksHalted):
            check_halt()
    finally:
        init_halt(None)


def test_falls_back_to_memory_when_no_db_configured():
    """CLI tools and tests that never init still behave."""
    init_halt(None)
    clear_halt()
    assert is_halted() is False
    set_halt("x")
    assert is_halted() is True
    clear_halt()
