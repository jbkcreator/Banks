"""Freezing a company: never on a paraphrase alone, never for a company we
don't track, and never silently.

Both failures were seen live on 2026-09-02:
  - "replied Evolve stop chasing them" wrote a freeze for the company
    "evolve stop chasing them" and told Josh "🧊 Froze Evolve". The row existed;
    the real Evolve kept its cadence.
  - "let's put a pin in Acme for now" matched no regex, fell through to the
    read-only QA layer, and Josh got a conversational reply while follow-ups
    kept firing — he believed he'd given an instruction.
"""
from __future__ import annotations

import sqlite3

import pytest

from banks.commands import Command, route
from banks.confirm import (clear_pending_confirmation, confirmation_prompt,
                           get_pending_confirmation, read_confirmation,
                           resolve_known_company, set_pending_confirmation)
from banks.socket_listener import _apply_freeze, _resolve_pending_confirmation
from banks.store import init_db


class Cfg:
    def __init__(self, db_path):
        self.db_path = db_path


class FakeWeb:
    def __init__(self):
        self.posts = []

    def chat_postMessage(self, **kw):
        self.posts.append(kw)


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "f.db")
    init_db(p)
    with sqlite3.connect(p) as c:
        for slug in ("evolve", "ketch"):
            c.execute("INSERT INTO opportunities (title, company_normalized, source, "
                      "status) VALUES (?,?,?,?)", ("AE", slug, "simplify", "applied"))
    return p


def _frozen(db):
    with sqlite3.connect(db) as c:
        return [r[0] for r in c.execute("SELECT company_normalized FROM company_freeze")]


# --- the garbage-company guard -------------------------------------------

def test_greedy_capture_resolves_to_the_real_company(db):
    assert resolve_known_company(db, "evolve stop chasing them")[0] == "evolve"


def test_untracked_company_is_never_frozen(db):
    cmd = Command("replied", "Acme", raw="replied Acme")
    reply = _apply_freeze(Cfg(db), cmd, "U1")
    assert "don't have any opportunity" in reply or "don't track" in reply
    assert _frozen(db) == []


def test_typo_is_offered_not_guessed_into_a_freeze(db):
    cmd = Command("replied", "Evolv", raw="replied Evolv")
    reply = _apply_freeze(Cfg(db), cmd, "U1")
    # Either resolved to the real company or offered as a suggestion —
    # what must never happen is a freeze row for "evolv".
    assert "evolv" not in _frozen(db)


# --- exact commands still act immediately ---------------------------------

def test_exact_command_freezes_without_asking(db):
    cmd = Command("replied", "Evolve", raw="replied Evolve", source="keyword")
    reply = _apply_freeze(Cfg(db), cmd, "U1")
    assert "evolve" in _frozen(db)
    assert "🧊" in reply
    assert get_pending_confirmation(db, "U1") is None


# --- inferred intent must be confirmed ------------------------------------

def test_inferred_freeze_asks_first_and_writes_nothing(db):
    cmd = Command("stop_company", "Evolve", raw="put a pin in Evolve", source="llm")
    reply = _apply_freeze(Cfg(db), cmd, "U1")
    assert "confirm" in reply.lower() and "evolve" in reply.lower()
    assert _frozen(db) == []                      # nothing written yet
    assert get_pending_confirmation(db, "U1")["company"] == "evolve"


def test_yes_applies_the_pending_freeze(db):
    set_pending_confirmation(db, "U1", "stop_company", "evolve", "put a pin in it")
    web = FakeWeb()
    consumed = _resolve_pending_confirmation(Cfg(db), web, "yes", "U1", "C1", None)
    assert consumed is True
    assert "evolve" in _frozen(db)
    assert get_pending_confirmation(db, "U1") is None


def test_no_drops_it_and_leaves_the_company_running(db):
    set_pending_confirmation(db, "U1", "stop_company", "evolve", "")
    web = FakeWeb()
    assert _resolve_pending_confirmation(Cfg(db), web, "no", "U1", "C1", None) is True
    assert _frozen(db) == []
    assert "leaving" in web.posts[0]["text"].lower()


def test_ambiguous_reply_is_not_consent(db):
    """Silence-adjacent replies must not freeze. The proposal is dropped and the
    message falls through to normal routing."""
    set_pending_confirmation(db, "U1", "stop_company", "evolve", "")
    web = FakeWeb()
    consumed = _resolve_pending_confirmation(Cfg(db), web, "what's my pipeline",
                                             "U1", "C1", None)
    assert consumed is False
    assert _frozen(db) == []
    assert get_pending_confirmation(db, "U1") is None


def test_expired_confirmation_is_not_applied(db, monkeypatch):
    import banks.confirm as confirm
    import datetime as dt
    set_pending_confirmation(db, "U1", "stop_company", "evolve", "")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        minutes=confirm.CONFIRM_TTL_MIN + 1)
    monkeypatch.setattr(confirm, "_now", lambda: later)
    assert get_pending_confirmation(db, "U1") is None
    assert _frozen(db) == []


def test_confirmation_words():
    assert read_confirmation("yes") is True
    assert read_confirmation("Yeah!") is True
    assert read_confirmation("no") is False
    assert read_confirmation("nah") is False
    assert read_confirmation("what about Ketch") is None


# --- the router half ------------------------------------------------------

def test_pronoun_company_asks_instead_of_freezing(db):
    """'stop chasing them' has no antecedent — ask, never freeze on a pronoun."""
    cmd = Command("stop_company", "them", raw="ok stop chasing them")
    reply = _apply_freeze(Cfg(db), cmd, "U1")
    assert "which company" in reply.lower()
    assert _frozen(db) == []


def test_llm_pronoun_company_is_not_actionable(db):
    class LLM:
        def extract_json(self, *a, **k):
            return {"intent": "stop_company", "company": "them"}
    # The LLM branch drops it outright (no company to act on).
    assert route(db, "please halt outreach for them", LLM()).intent == "none"


def test_llm_mutation_is_marked_as_inferred(db):
    class LLM:
        def extract_json(self, *a, **k):
            return {"intent": "stop_company", "company": "Evolve"}
    cmd = route(db, "let's put a pin in Evolve for now", LLM())
    assert cmd.intent == "stop_company" and cmd.source == "llm"
