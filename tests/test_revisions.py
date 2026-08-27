"""MOD-05 threaded NL revisions + embellishment guard + listener precedence."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.config import BanksConfig
from banks.llmport import FakeLLMPort
from banks.opportunity import CareerFacts
from banks.revisions import (
    apply_revision,
    classify_revision,
    is_revision_context,
)
from banks.socket_listener import classify_incoming, is_authorized
from banks.store import cursor, init_db

FACTS = CareerFacts(
    identity="GTM leader with experience building sales orgs",
    experience=("VP Sales at PropTech Co",),
    skills=("enterprise sales", "GTM strategy"),
    seeking="VP Sales roles",
)


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _pending_draft(db_path, ref="42", body="Hi, I'm interested in the role.", card_ts="111.0"):
    """A pending decision packet + intent + queue_items row with a card_ts."""
    now = _now()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO decision_packets (kind, decision, recommendation, "
            "default_if_unanswered, created_at) VALUES ('outreach', 'd', 'r', 'skip', ?)",
            (now,),
        )
        pid = cur.lastrowid
        ref = str(pid)
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, status, created_at) "
            "VALUES (?, 'none:internal', 'x@y.com', 'Interest', ?, 'pending', ?)",
            (ref, body, now),
        )
        cur.execute(
            "INSERT INTO queue_items (draft_ref, category, state, first_surfaced_at, last_surfaced_at, card_ts) "
            "VALUES (?, 'tier_a', 'active', ?, ?, ?)",
            (ref, now, now, card_ts),
        )
    return ref


# --- revision context gate --------------------------------------------------

def test_is_revision_context_maps_card_ts(db_path):
    ref = _pending_draft(db_path, card_ts="222.0")
    assert is_revision_context(db_path, "222.0") == ref


def test_is_revision_context_unknown_ts(db_path):
    _pending_draft(db_path, card_ts="222.0")
    assert is_revision_context(db_path, "999.9") is None


# --- classify (keyword + none) ----------------------------------------------

def test_classify_revise_keyword():
    intent, instruction = classify_revision("make it shorter")
    assert intent == "revise"


def test_classify_non_revise_silent_without_llm():
    intent, _ = classify_revision("who is this again?")
    assert intent == "none"


def test_classify_llm_fallback_question():
    llm = FakeLLMPort({"who is this": '{"intent": "question", "instruction": null}'})
    intent, _ = classify_revision("who is this again?", llm=llm)
    assert intent == "question"


# --- apply_revision ---------------------------------------------------------

def test_apply_revision_redrafts_in_place(db_path):
    ref = _pending_draft(db_path, body="Hi, I am interested in the VP Sales role.")
    llm = FakeLLMPort({"instruction": "Interested in VP Sales. Let's talk."})
    chat = FakeChatPort()
    res = apply_revision(db_path, ref, "shorter", FACTS, llm, chat)
    assert res["ok"] is True
    # send_intent body updated in place (same draft_ref / packet)
    with cursor(db_path) as cur:
        body = cur.execute("SELECT body FROM send_intents WHERE draft_ref = ?", (ref,)).fetchone()["body"]
    assert body == "Interested in VP Sales. Let's talk."
    assert chat.posts  # re-posted


def test_apply_revision_flags_embellishment(db_path):
    ref = _pending_draft(db_path, body="Hi, I'm interested in the role.")
    # LLM tries to punch it up with a fabricated number not in facts/original
    llm = FakeLLMPort({"instruction": "I drove $50M in ARR growth — let's talk."})
    chat = FakeChatPort()
    res = apply_revision(db_path, ref, "stronger hook", FACTS, llm, chat)
    assert res["ok"] is False
    assert res["reason"] == "embellishment"
    assert "50" in res["detail"]
    # original draft untouched, nothing re-posted
    with cursor(db_path) as cur:
        body = cur.execute("SELECT body FROM send_intents WHERE draft_ref = ?", (ref,)).fetchone()["body"]
    assert body == "Hi, I'm interested in the role."
    assert chat.posts == []


def test_apply_revision_no_pending_draft(db_path):
    res = apply_revision(db_path, "999", "shorter", FACTS, FakeLLMPort(), FakeChatPort())
    assert res["ok"] is False and res["reason"] == "no_pending_draft"


# --- listener precedence + single-approver ----------------------------------

def test_precedence_halt_first():
    # even inside a pending thread, a halt phrase wins
    assert classify_incoming("stop banks", has_pending_thread=True) == "halt"


def test_precedence_revise_in_pending_thread():
    assert classify_incoming("make it shorter", has_pending_thread=True) == "revise"


def test_precedence_command_top_level():
    assert classify_incoming("who do I know at Acme", has_pending_thread=False) == "command"


def test_precedence_ignore_empty():
    assert classify_incoming("   ", has_pending_thread=False) == "ignore"


def test_single_approver_lock():
    cfg = BanksConfig(slack_bot_token=None, slack_channel_id=None, approver_user_id="UJOSH")
    assert is_authorized(cfg, "UJOSH") is True
    assert is_authorized(cfg, "USOMEONE") is False


def test_single_approver_none_allows_all():
    cfg = BanksConfig(slack_bot_token=None, slack_channel_id=None, approver_user_id=None)
    assert is_authorized(cfg, "anyone") is True
