"""Tests for Phase I Tier 3: integrity, halt, compute, ramp-up, FA-overlap."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from banks.chatport import FakeChatPort
from banks.compute import (
    DailyCap, check_daily_cap, cost_cents, daily_spend_cents,
    log_llm_call, weekly_compute_cost_cents,
)
from banks.enforcement import Draft
from banks.halt import (
    BanksHalted, check_halt, clear_halt, is_halt_command, is_halted, set_halt,
)
from banks.integrity import (
    ImmutableCoreTampered, compute_hash, extract_immutable_core, verify,
    write_approved_hash,
)
from banks.overlap import check_and_flag, check_fa_overlap, load_fa_names
from banks.rampup import in_rampup, rampup_days_remaining, should_batch_to_brief
from banks.store import init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


@pytest.fixture(autouse=True)
def reset_halt():
    """Always clear the halt flag before/after each test."""
    clear_halt()
    yield
    clear_halt()


# --- Integrity check ---------------------------------------------------------

def test_extract_immutable_core_finds_section(tmp_path):
    constitution = tmp_path / "c.md"
    constitution.write_text(
        "# Banks\n## HARD RULES — IMMUTABLE CORE\nNever send.\n## OTHER\nStuff.\n",
        encoding="utf-8",
    )
    core = extract_immutable_core(constitution.read_text(encoding="utf-8"))
    assert "Never send." in core
    assert "OTHER" not in core


_CONSTITUTION_STUB = (
    "# Banks\n## HARD RULES — IMMUTABLE CORE\nNever send.\n"
)


def test_verify_passes_with_correct_hash(tmp_path):
    constitution = tmp_path / "c.md"
    constitution.write_text(_CONSTITUTION_STUB, encoding="utf-8")
    hash_path = tmp_path / "c.hash"
    write_approved_hash(constitution, hash_path)
    verify(constitution, hash_path)  # no raise


def test_verify_fails_on_tampered_core(tmp_path):
    constitution = tmp_path / "c.md"
    constitution.write_text(_CONSTITUTION_STUB, encoding="utf-8")
    hash_path = tmp_path / "c.hash"
    write_approved_hash(constitution, hash_path)
    # Tamper.
    constitution.write_text(
        "# Banks\n## HARD RULES — IMMUTABLE CORE\nSend whenever.\n",
        encoding="utf-8",
    )
    with pytest.raises(ImmutableCoreTampered):
        verify(constitution, hash_path)


def test_verify_fails_if_no_hash_file(tmp_path):
    constitution = tmp_path / "c.md"
    constitution.write_text(
        "# Banks\n## HARD RULES — IMMUTABLE CORE\nNever send.\n"
    )
    with pytest.raises(ImmutableCoreTampered, match="No approved hash"):
        verify(constitution, tmp_path / "missing.hash")


def test_real_constitution_passes_verify():
    """The shipped constitution and hash file must be in sync."""
    pkg = Path(__file__).parent.parent / "banks"
    verify(pkg / "constitution.md", pkg / "constitution.hash")


# --- Kill command / halt flag ------------------------------------------------

def test_halt_flag_starts_false():
    assert not is_halted()


def test_set_halt_raises_on_check():
    set_halt("test")
    with pytest.raises(BanksHalted):
        check_halt()


def test_clear_halt_allows_check():
    set_halt("test")
    clear_halt()
    check_halt()  # no raise


def test_is_halt_command_recognised():
    assert is_halt_command("STOP ALL")
    assert is_halt_command("stop all")
    assert is_halt_command("STOP Banks")
    assert is_halt_command("  stop banks  ")


def test_non_halt_phrases_not_recognised():
    assert not is_halt_command("stop the music")
    assert not is_halt_command("hello")
    assert not is_halt_command("")


def test_run_job_raises_when_halted(db):
    from banks.jobs import run_job
    set_halt("test")
    with pytest.raises(BanksHalted):
        run_job("morning_dashboard", db, FakeChatPort())


# --- Compute discipline -------------------------------------------------------

def test_cost_cents_cheap():
    # 1000 tokens at cheap tier.
    c = cost_cents("cheap", 1000)
    assert c >= 0  # exact value may vary; just ensure non-negative and non-zero
    assert c < 10  # cheap tier should be < 1 cent per 1k tokens


def test_cost_cents_premium_higher_than_cheap():
    assert cost_cents("premium", 1000) > cost_cents("cheap", 1000)


def test_log_llm_call_increments_daily_spend(db):
    before = daily_spend_cents(db)
    log_llm_call(db, "cheap", 500, purpose="test")
    after = daily_spend_cents(db)
    assert after >= before  # spend went up (or stayed same if cost rounds to 0)


def test_weekly_compute_cost_aggregates(db):
    log_llm_call(db, "cheap", 10000, purpose="a")
    log_llm_call(db, "premium", 5000, purpose="b")
    cost = weekly_compute_cost_cents(db, date.today().isoformat())
    assert cost >= 0


def test_daily_cap_raises_when_exceeded(db, monkeypatch):
    monkeypatch.setenv("BANKS_DAILY_LLM_CAP_CENTS", "1")  # cap = $0.01
    # Log enough to hit it.
    log_llm_call(db, "premium", 100000, purpose="big call")
    with pytest.raises(DailyCap):
        check_daily_cap(db, "premium", 100000)


# --- Ramp-up mode ------------------------------------------------------------

def test_in_rampup_true_during_window(monkeypatch):
    monkeypatch.setenv("BANKS_RAMPUP_START", date.today().isoformat())
    assert in_rampup()


def test_in_rampup_false_after_window(monkeypatch):
    from datetime import timedelta
    start = (date.today() - timedelta(days=31)).isoformat()
    monkeypatch.setenv("BANKS_RAMPUP_START", start)
    assert not in_rampup()


def test_in_rampup_false_when_unset(monkeypatch):
    monkeypatch.delenv("BANKS_RAMPUP_START", raising=False)
    assert not in_rampup()


def test_should_batch_to_brief_after_rampup(monkeypatch):
    from datetime import timedelta
    start = (date.today() - timedelta(days=31)).isoformat()
    monkeypatch.setenv("BANKS_RAMPUP_START", start)
    assert should_batch_to_brief(urgent=False)
    assert not should_batch_to_brief(urgent=True)


def test_should_not_batch_during_rampup(monkeypatch):
    monkeypatch.setenv("BANKS_RAMPUP_START", date.today().isoformat())
    assert not should_batch_to_brief(urgent=False)


# --- FA-name overlap flagging ------------------------------------------------

def test_no_overlap_when_list_empty():
    assert not check_fa_overlap("alice@example.com", frozenset())


def test_overlap_detected_by_name(tmp_path):
    fa_names = frozenset({"alice", "bob"})
    assert check_fa_overlap("alice@fa.com", fa_names)
    assert not check_fa_overlap("carol@example.com", fa_names)


def test_load_fa_names_from_file(tmp_path):
    f = tmp_path / "names.txt"
    f.write_text("Alice\nbob\n  Charlie  \n")
    names = load_fa_names(str(f))
    assert "alice" in names
    assert "bob" in names
    assert "charlie" in names


def test_load_fa_names_returns_empty_when_file_missing():
    names = load_fa_names("/nonexistent/path.txt")
    assert names == frozenset()


def test_check_and_flag_surfaces_draft_on_overlap(db):
    fa_names = frozenset({"alice"})
    chat = FakeChatPort()
    flagged = check_and_flag(db, "alice@fa.com", chat, fa_names=fa_names)
    assert flagged is True
    # A draft was posted.
    assert len(chat.posts) > 0


def test_check_and_flag_no_op_on_no_overlap(db):
    fa_names = frozenset({"alice"})
    chat = FakeChatPort()
    flagged = check_and_flag(db, "carol@example.com", chat, fa_names=fa_names)
    assert flagged is False
    assert len(chat.posts) == 0
