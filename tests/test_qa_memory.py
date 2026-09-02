"""Follow-up questions must resolve — and when they can't, Banks asks.

"what's the status of Ketch" → "who do I know there" used to have no referent
(every mention was answered from scratch), so the model re-asked or guessed a
company. Guessing is the branch that matters: a confident answer about the
wrong employer is worse than one more question.
"""
from __future__ import annotations

import pytest

from banks.qa import answer_question
from banks.qa_memory import (companies_in_context, is_referring,
                             needs_clarification, record_turn, recent_turns,
                             resolve_company, MAX_TURNS)
from banks.store import init_db


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "qa.db")
    init_db(p)
    return p


class FakeLLM:
    """Scripted router + composer."""
    def __init__(self, routings):
        self.routings = list(routings)
        self.prompts = []

    def extract_json(self, system, user, schema_hint=None):
        self.prompts.append(user)
        return self.routings.pop(0) if self.routings else {"tool": "done"}

    def complete(self, system, user, max_tokens=600):
        self.prompts.append(user)
        return "composed answer"


# --- memory ---------------------------------------------------------------

def test_turn_is_recorded_and_read_back(db):
    record_turn(db, "U1", "status of Ketch", "Tier A", ["ketch"])
    turns = recent_turns(db, "U1")
    assert len(turns) == 1 and turns[0]["question"] == "status of Ketch"


def test_history_is_per_user(db):
    record_turn(db, "U1", "q1", "a1", ["ketch"])
    assert recent_turns(db, "U2") == []


def test_history_is_capped(db):
    for i in range(MAX_TURNS + 4):
        record_turn(db, "U1", f"q{i}", "a", [])
    assert len(recent_turns(db, "U1")) == MAX_TURNS


def test_stale_turns_are_not_context(db):
    """A company from an hour ago is a stale assumption, not context."""
    import banks.qa_memory as mem
    from datetime import datetime, timedelta, timezone
    record_turn(db, "U1", "status of Ketch", "Tier A", ["ketch"])
    old = datetime.now(timezone.utc) + timedelta(minutes=mem.TURN_TTL_MIN + 5)
    mem_now = mem._now
    mem._now = lambda: old
    try:
        assert recent_turns(db, "U1") == []
    finally:
        mem._now = mem_now


def test_companies_in_context_is_most_recent_first(db):
    record_turn(db, "U1", "q", "a", ["ketch"])
    record_turn(db, "U1", "q", "a", ["evolve"])
    assert companies_in_context(db, "U1")[0] == "evolve"


def test_is_referring_detects_pronouns():
    assert is_referring("who do I know there")
    assert is_referring("ok stop chasing them")
    assert not is_referring("who do I know at Ketch")


# --- the no-guess rule ----------------------------------------------------

def test_asks_when_no_company_anywhere():
    assert needs_clarification("who_do_i_know", {}, []) == "Which company do you mean?"


def test_asks_which_when_context_is_ambiguous():
    ask = needs_clarification("company_status", {}, ["ketch", "evolve"])
    assert "ketch" in ask and "evolve" in ask


def test_proceeds_when_context_is_unambiguous():
    assert needs_clarification("who_do_i_know", {}, ["ketch"]) is None
    assert resolve_company({}, ["ketch"])["company"] == "ketch"


def test_explicit_company_always_wins():
    assert needs_clarification("who_do_i_know", {"company": "Flex"}, ["ketch"]) is None
    assert resolve_company({"company": "Flex"}, ["ketch"])["company"] == "Flex"


def test_company_free_tools_never_ask():
    for tool in ("pipeline_summary", "call_list", "list_opportunities", "recent_email"):
        assert needs_clarification(tool, {}, []) is None


# --- end to end -----------------------------------------------------------

def test_followup_resolves_there_from_previous_turn(db):
    record_turn(db, "U1", "what's the status of Ketch?", "Ketch — Tier A", ["ketch"])
    llm = FakeLLM([{"tool": "who_do_i_know", "args": {}}])
    out = answer_question(db, "who do I know there?", llm, user_id="U1")
    assert out == "composed answer"          # answered, not deflected
    assert any("Ketch" in p for p in llm.prompts)   # context reached the model


def test_ambiguous_followup_asks_instead_of_guessing(db):
    record_turn(db, "U1", "status of Ketch", "…", ["ketch"])
    record_turn(db, "U1", "status of Evolve", "…", ["evolve"])
    llm = FakeLLM([{"tool": "who_do_i_know", "args": {}}])
    out = answer_question(db, "who do I know there?", llm, user_id="U1")
    assert "Which one" in out and "ketch" in out and "evolve" in out


def test_cold_start_asks_rather_than_guessing(db):
    llm = FakeLLM([{"tool": "company_status", "args": {}}])
    out = answer_question(db, "how's it going there?", llm, user_id="U1")
    assert out == "Which company do you mean?"


def test_router_clarify_verdict_is_honoured(db):
    llm = FakeLLM([{"tool": "clarify"}])
    assert answer_question(db, "is this worth pursuing?", llm, user_id="U1") == \
        "Which company do you mean?"


def test_answer_is_recorded_for_the_next_turn(db):
    llm = FakeLLM([{"tool": "company_status", "args": {"company": "Ketch"}}])
    answer_question(db, "status of Ketch", llm, user_id="U1")
    assert "ketch" in companies_in_context(db, "U1")


def test_memory_failure_never_breaks_the_answer(db, monkeypatch):
    import banks.qa_memory as mem
    monkeypatch.setattr(mem, "record_turn",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    llm = FakeLLM([{"tool": "pipeline_summary", "args": {}}])
    assert answer_question(db, "where am I", llm, user_id="U1") == "composed answer"


def test_anonymous_caller_still_works(db):
    llm = FakeLLM([{"tool": "pipeline_summary", "args": {}}])
    assert answer_question(db, "where am I", llm) == "composed answer"
