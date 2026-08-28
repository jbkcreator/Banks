"""MOD-05 button-driven revision: pending slot, precedence, listener flow."""
from __future__ import annotations

import datetime as dt
import dataclasses
import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.config import load_config
from banks.llmport import FakeLLMPort
from banks.opportunity import CareerFacts, load_career_facts
from banks.revisions import (
    clear_pending_revision,
    get_pending_revision,
    set_pending_revision,
)
from banks.socket_listener import (
    _handle_action,
    _handle_message,
    classify_incoming,
)
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _pending_draft(db_path, body="Hi, I'm interested in the role.", card_ts="111.0"):
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


class _FakeWeb:
    def __init__(self):
        self.updates = []
        self.posts = []

    def chat_update(self, **kw):
        self.updates.append(kw)
        return {"ok": True}

    def chat_postMessage(self, **kw):
        self.posts.append(kw)
        return {"ok": True}


def _cfg(db_path):
    # approver None → allow anyone (test), so is_authorized passes for "UJOSH"
    return dataclasses.replace(load_config(), db_path=db_path,
                              slack_channel_id="C", slack_jobs_channel_id="CJOBS")


# --- pending slot mechanics -------------------------------------------------

def test_set_get_clear(db_path):
    set_pending_revision(db_path, "U1", "42")
    assert get_pending_revision(db_path, "U1") == "42"
    clear_pending_revision(db_path, "U1")
    assert get_pending_revision(db_path, "U1") is None


def test_last_tap_wins(db_path):
    set_pending_revision(db_path, "U1", "42")
    set_pending_revision(db_path, "U1", "51")
    assert get_pending_revision(db_path, "U1") == "51"


def test_expiry_clears(db_path):
    set_pending_revision(db_path, "U1", "42")
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=20)
    assert get_pending_revision(db_path, "U1", now=future) is None
    # cleared as a side effect
    assert get_pending_revision(db_path, "U1") is None


# --- precedence -------------------------------------------------------------

def test_precedence_halt_beats_pending():
    assert classify_incoming("stop banks", has_pending_revision=True) == "halt"


def test_precedence_pending_is_revise():
    assert classify_incoming("shorter", has_pending_revision=True) == "revise"


def test_precedence_no_pending_is_command():
    assert classify_incoming("who do I know at Acme", has_pending_revision=False) == "command"


# --- listener flow ----------------------------------------------------------

def test_revise_button_sets_pending(db_path):
    ref = _pending_draft(db_path)
    payload = {"actions": [{"action_id": "banks_revise", "value": ref}],
               "user": {"id": "UJOSH"}, "channel": {"id": "C"},
               "message": {"ts": "1.0"}}
    _handle_action(_cfg(db_path), _FakeWeb(), payload)
    assert get_pending_revision(db_path, "UJOSH") == ref


def test_next_message_applies_revision(db_path):
    ref = _pending_draft(db_path, body="Hi, I am interested in the VP Sales role.")
    set_pending_revision(db_path, "UJOSH", ref)
    llm = FakeLLMPort({"Instruction": "Interested in VP Sales. Let's talk."})
    web = _FakeWeb()
    event = {"type": "message", "text": "shorter", "user": "UJOSH", "channel": "C"}
    _handle_message(_cfg(db_path), web, llm, FakeChatPort(), event)
    # slot consumed
    assert get_pending_revision(db_path, "UJOSH") is None
    # draft rewritten in place
    with cursor(db_path) as cur:
        body = cur.execute("SELECT body FROM send_intents WHERE draft_ref = ?", (ref,)).fetchone()["body"]
    assert body == "Interested in VP Sales. Let's talk."
    assert any("Revised" in p.get("text", "") for p in web.posts)


def test_cancel_clears_pending(db_path):
    ref = _pending_draft(db_path)
    set_pending_revision(db_path, "UJOSH", ref)
    web = _FakeWeb()
    event = {"type": "message", "text": "cancel", "user": "UJOSH", "channel": "C"}
    _handle_message(_cfg(db_path), web, FakeLLMPort(), FakeChatPort(), event)
    assert get_pending_revision(db_path, "UJOSH") is None
    assert any("cancelled" in p.get("text", "").lower() for p in web.posts)


def test_non_approver_message_does_not_consume_slot(db_path):
    ref = _pending_draft(db_path)
    set_pending_revision(db_path, "UJOSH", ref)
    cfg = dataclasses.replace(_cfg(db_path), approver_user_id="UJOSH")
    web = _FakeWeb()
    # a different user types — must not consume Josh's pending slot
    event = {"type": "message", "text": "shorter", "user": "USOMEONE", "channel": "C"}
    _handle_message(cfg, web, FakeLLMPort(), FakeChatPort(), event)
    assert get_pending_revision(db_path, "UJOSH") == ref


# --- career-facts loader ----------------------------------------------------

def test_load_career_facts_empty_file():
    # the repo's career-facts.md is placeholder-only → empty
    facts = load_career_facts()
    assert isinstance(facts, CareerFacts)
    assert facts.is_empty()


def test_load_career_facts_missing_file():
    assert load_career_facts("does/not/exist.md").is_empty()


def test_load_career_facts_parses_sections():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "cf.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "## Identity\nJosh Kantor, GTM exec\n\n"
            "## Experience\n- VP Sales at PropTech Co\n- Director GTM at SaaS Co\n\n"
            "## Skills\n- enterprise sales\n\n"
            "## What Josh is looking for\nVP Sales / CRO roles\n"
        )
    facts = load_career_facts(path)
    assert not facts.is_empty()
    assert "Josh Kantor" in facts.identity
    assert facts.experience == ("VP Sales at PropTech Co", "Director GTM at SaaS Co")
    assert facts.skills == ("enterprise sales",)
    assert facts.seeking == "VP Sales / CRO roles"
