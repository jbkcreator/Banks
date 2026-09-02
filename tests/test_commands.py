"""MOD-05 hybrid command router + handlers + rate limiter."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.commands import (Command, RateLimiter, fallback_reply,
                            handle_command, route)
from banks.llmport import FakeLLMPort
from banks.opportunity import record_opportunity
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _contact(db_path, name, company, source="linkedin_csv", title=None):
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, source, title, degree, added_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (name, company, source, title, _now()),
        )


# --- Layer 1 keyword fast-path (no LLM) -------------------------------------

def test_keyword_whoat(db_path):
    cmd = route(db_path, "who do I know at AppFolio")
    assert cmd == Command("whoat", "appfolio") or (cmd.intent == "whoat" and cmd.company.lower() == "appfolio")


def test_keyword_status(db_path):
    cmd = route(db_path, "status Acme")
    assert cmd.intent == "status" and cmd.company.lower() == "acme"


def test_keyword_calllist(db_path):
    assert route(db_path, "call list").intent == "calllist"
    assert route(db_path, "who should I reach out to today").intent == "calllist"


def test_keyword_none_without_llm(db_path):
    assert route(db_path, "good morning banks").intent == "none"


# --- Layer 2 LLM fallback ---------------------------------------------------

def test_llm_fallback_typo_phrasing(db_path):
    # phrasing the keyword layer won't catch
    llm = FakeLLMPort({"appfolio": '{"intent": "whoat", "company": "AppFolio"}'})
    cmd = route(db_path, "anyone I'm connected with over at appfolio??", llm=llm)
    assert cmd.intent == "whoat"
    assert cmd.company.lower() == "appfolio"


def test_llm_fallback_none(db_path):
    llm = FakeLLMPort({"weather": '{"intent": "none", "company": null}'})
    assert route(db_path, "what's the weather", llm=llm).intent == "none"


# --- handlers ---------------------------------------------------------------

def test_handle_whoat(db_path):
    _contact(db_path, "Jane Smith", "appfolio", title="VP Sales")
    reply = handle_command(db_path, Command("whoat", "appfolio"))
    assert "Jane Smith" in reply


def test_handle_whoat_none_known(db_path):
    reply = handle_command(db_path, Command("whoat", "nobodyco"))
    assert "No known contacts" in reply


def test_handle_status_snapshot(db_path):
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             pursuit_mode="full_time", company_normalized="acme")
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, status, created_at) "
            "VALUES (?, 'hiring_manager', 'pending', ?)",
            (opp, _now()),
        )
    reply = handle_command(db_path, Command("status", "acme"))
    assert "Tier A" in reply
    assert "hiring_manager" in reply
    assert "Not frozen" in reply


def test_handle_status_unknown(db_path):
    assert "No opportunity tracked" in handle_command(db_path, Command("status", "ghostco"))


def test_handle_none_returns_help(db_path):
    reply = handle_command(db_path, Command("none"))
    assert "who do I know at" in reply


# --- rate limiter -----------------------------------------------------------

def test_rate_limiter_caps():
    rl = RateLimiter(max_calls=3, window_s=60.0)
    t = 100.0
    assert rl.allow("U1", now=t)
    assert rl.allow("U1", now=t)
    assert rl.allow("U1", now=t)
    assert rl.allow("U1", now=t) is False           # 4th within window → blocked
    assert rl.allow("U1", now=t + 61) is True        # window rolled → allowed


def test_rate_limiter_per_user():
    rl = RateLimiter(max_calls=1, window_s=60.0)
    assert rl.allow("U1", now=100.0)
    assert rl.allow("U2", now=100.0)                 # different user, own budget
    assert rl.allow("U1", now=100.0) is False


class TestUnrecognisedFallback:
    """An unrecognised message must NOT dump the same menu every time; it should
    answer honestly (esp. capability questions Banks can't do)."""

    def test_linkedin_question_gets_honest_reply(self, db_path):
        cmd = route(db_path, "Can you see LinkedIn?")
        assert cmd.intent == "cant_do"
        reply = handle_command(db_path, cmd)
        assert "can't browse linkedin" in reply.lower()

    def test_linkedin_typo_still_caught(self, db_path):
        assert route(db_path, "can u see linkdin").intent == "cant_do"

    def test_look_through_gmail_reply_is_accurate(self, db_path):
        """Banks DOES read job-search mail (Josh granted access 2026-09-02), so
        the reply must not claim otherwise — while still ruling out LinkedIn."""
        reply = handle_command(db_path, route(db_path,
            "Can you look through my slack, LinkedIn and Gmail?")).lower()
        assert "can't browse linkedin" in reply
        assert "can't read your live inbox" not in reply
        assert "job-search mail" in reply

    def test_email_question_is_not_refused(self, db_path):
        """'did anyone email me back' must fall through to the QA layer's
        recent_email tool, not get the old blanket 'I can't' refusal."""
        assert route(db_path, "did anyone email me back").intent != "cant_do"

    def test_explicit_help_shows_menu(self, db_path):
        reply = handle_command(db_path, route(db_path, "help"))
        assert "who do I know at" in reply and "call list" in reply

    def test_greeting_is_short_not_menu(self, db_path):
        reply = fallback_reply("Good morning banks")
        assert "who do I know at" not in reply
        assert "help" in reply.lower()

    def test_random_miss_is_one_line_nudge(self, db_path):
        reply = fallback_reply("asdfqwer")
        assert "not sure" in reply.lower()


class TestPipelineSnapshot:
    def test_where_am_i_routes_to_pipeline(self, db_path):
        assert route(db_path, "where am I with applying?").intent == "pipeline"
        assert route(db_path, "give me an update on my applications").intent == "pipeline"

    def test_pipeline_empty(self, db_path):
        reply = handle_command(db_path, Command("pipeline"))
        assert "No applications tracked yet" in reply

    def test_pipeline_counts(self, db_path):
        record_opportunity(db_path, "VP Sales", "x", 90, tier="A",
                           company_normalized="acme", needs_enrichment=0)
        record_opportunity(db_path, "AE", "x", 55, tier="B",
                           company_normalized="beta", needs_enrichment=1)
        reply = handle_command(db_path, Command("pipeline"))
        assert "2 opportunities tracked" in reply
        assert "Tier A 1" in reply
        assert "Held for enrichment" in reply


class TestTargetedStopAndHalt:
    def test_stop_chasing_routes_to_company_freeze(self, db_path):
        assert route(db_path, "stop chasing Acme").intent == "stop_company"
        assert route(db_path, "stop Beta").intent == "stop_company"

    def test_stop_company_freezes(self, db_path):
        record_opportunity(db_path, "VP", "x", 80, tier="A",
                           company_normalized="acme")
        reply = handle_command(db_path, Command("stop_company", "acme"))
        assert "Stopped chasing" in reply
        from banks.governance import is_company_frozen
        assert is_company_frozen(db_path, "acme")

    def test_global_halt_not_swallowed_by_targeted(self):
        from banks.halt import is_halt_command
        assert is_halt_command("stop everything") is True
        assert is_halt_command("STOP ALL!") is True
        assert is_halt_command("stpo all") is True
        assert is_halt_command("stop chasing Acme") is False
        assert is_halt_command("freeze Acme") is False

    def test_unhalt_matches_resume_words(self):
        from banks.halt import is_unhalt_command
        assert is_unhalt_command("resume")
        assert is_unhalt_command("start banks")
        assert is_unhalt_command("where am I") is False
