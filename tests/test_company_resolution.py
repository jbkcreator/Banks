"""Company resolution: the anti-garbage guard on freezes + typo tolerance.

Two live failures drove this file (2026-09-02):

  1. `@banks replied Evolve — stop chasing them` wrote a company_freeze row keyed
     "evolve — stop chasing them" and answered "🧊 Froze … follow-ups stopped",
     while `evolve` itself was never frozen and kept getting chased. A greedy
     `(.+)` capture plus an unvalidated write.
  2. Soft phrasings ("I don't think I want to keep chasing Acme") matched no
     regex, fell through to the read-only QA layer, and silently did nothing —
     Josh believes he stopped a company that is still running.

The rule these tests pin: a freeze is written ONLY for a company Banks actually
tracks, named explicitly, on a deterministic phrasing. Anything else asks.
"""
from __future__ import annotations

import pytest

from banks.commands import (Command, _clean_company, handle_command,
                            is_pronoun_reference, resolve_company, route,
                            who_do_i_know_text)
from banks.llmport import FakeLLMPort
from banks.opportunity import record_opportunity
from banks.store import cursor


@pytest.fixture
def db(db_path):
    record_opportunity(db_path, "Principal Partnerships Manager", "simplify", 80,
                       tier="A", company_normalized="evolve", status="applied")
    record_opportunity(db_path, "VP of Sales", "simplify", 70,
                       tier="B", company_normalized="appfolio", status="applied")
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, source, degree, added_at) "
            "VALUES ('Dana Reeve', 'rippling', 'linkedin_csv', 1, '2026-01-01')")
    return db_path


def _freezes(db_path) -> set[str]:
    with cursor(db_path) as cur:
        return {r["company_normalized"]
                for r in cur.execute("SELECT company_normalized FROM company_freeze")}


# --- 1. the greedy-capture bug that reached production ----------------------

def test_clean_company_cuts_at_clause_boundary():
    assert _clean_company("evolve — stop chasing them") == "evolve"
    assert _clean_company("acme corp, they ghosted me") == "acme corp"
    assert _clean_company("appfolio for now") == "appfolio"
    assert _clean_company("acme please thanks") == "acme"


def test_replied_with_trailing_prose_freezes_the_real_company(db):
    """The exact live input. It must freeze `evolve`, not invent a company."""
    cmd = route(db, "replied Evolve — stop chasing them")
    reply = handle_command(db, cmd)
    assert _freezes(db) == {"evolve"}
    assert "evolve — stop chasing them" not in _freezes(db)
    assert "🧊" in reply


# --- 2. pronouns must ask, never write --------------------------------------

def test_pronoun_reference_detected():
    assert is_pronoun_reference("them")
    assert is_pronoun_reference("The First One")
    assert not is_pronoun_reference("appfolio")


def test_stop_chasing_them_writes_nothing_and_asks(db):
    """Previously froze a company literally named "them" and confirmed success."""
    cmd = route(db, "ok stop chasing them")
    reply = handle_command(db, cmd)
    assert _freezes(db) == set()
    assert "🧊" not in reply
    assert "which company" in reply.lower()


# --- 3. an untracked company must not create a row --------------------------

def test_unknown_company_writes_nothing(db):
    reply = handle_command(db, Command("stop_company", "zzz widgets inc"))
    assert _freezes(db) == set()
    assert "don't track" in reply.lower()


def test_near_miss_suggests_instead_of_freezing(db):
    """A typo on a MUTATION must confirm — a freeze has no undo."""
    reply = handle_command(db, Command("stop_company", "appfolo"))
    assert _freezes(db) == set()
    assert "appfolio" in reply.lower()
    assert "did you mean" in reply.lower()


# --- 4. soft phrasing: proposes, never silently no-ops ----------------------

def test_llm_classified_stop_confirms_without_writing(db):
    """The sharp bug: soft phrasing used to fall through and do nothing."""
    llm = FakeLLMPort({"keep chasing": '{"intent":"stop_company","company":"Evolve"}'})
    cmd = route(db, "yeah I don't think I want to keep chasing Evolve, they ghosted me",
                llm)
    assert cmd.intent == "stop_company" and cmd.source == "llm"

    reply = handle_command(db, cmd)
    assert _freezes(db) == set()          # proposed, not applied
    assert "confirm" in reply.lower()
    assert "@banks stop chasing evolve" in reply.lower()
    assert "nothing has changed yet" in reply.lower()


def test_llm_classified_replied_confirms_without_writing(db):
    llm = FakeLLMPort({"got back to me": '{"intent":"replied","company":"Evolve"}'})
    cmd = route(db, "they got back to me finally, the Evolve recruiter replied", llm)
    assert cmd.intent == "replied" and cmd.source == "llm"
    reply = handle_command(db, cmd)
    assert _freezes(db) == set()
    assert "@banks replied evolve" in reply.lower()


def test_llm_mutation_without_a_company_is_not_actionable(db):
    """'drop them' names nobody — must not become a freeze on a guess."""
    llm = FakeLLMPort({"drop them": '{"intent":"stop_company","company":"them"}'})
    cmd = route(db, "ugh just drop them", llm)
    assert cmd.intent == "none"
    assert _freezes(db) == set()


def test_deterministic_phrasing_still_applies_immediately(db):
    """The explicit command must not regress into a confirmation loop."""
    cmd = route(db, "stop chasing AppFolio")
    assert cmd.source == "keyword"
    reply = handle_command(db, cmd)
    assert _freezes(db) == {"appfolio"}
    assert "🧊" in reply


# --- 5. typo tolerance on reads ---------------------------------------------

def test_resolve_company_exact_and_fuzzy(db):
    assert resolve_company(db, "AppFolio").exact
    m = resolve_company(db, "Ripling")
    assert not m.exact and "rippling" in m.suggestions


def test_status_typo_resolves_instead_of_dead_ending(db):
    reply = handle_command(db, Command("status", "AppFolo"))
    assert "no opportunity tracked" not in reply.lower()
    assert "appfolio" in reply.lower()
    assert "vp of sales" in reply.lower()


def test_who_do_i_know_typo_resolves(db):
    reply = who_do_i_know_text(db, "Ripling")
    assert "Dana Reeve" in reply
    assert "rippling" in reply.lower()


def test_status_unknown_company_still_says_so(db):
    assert "no opportunity tracked" in handle_command(
        db, Command("status", "zzz widgets")).lower()
