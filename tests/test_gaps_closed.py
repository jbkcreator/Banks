"""#1 reactions fallback, #3 redraft, #4 scheduler dispatch, #5 financial→email."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from banks.approval import ButtonAction
from banks.chatport import FakeChatPort
from banks.enforcement import Draft
from banks.flow import propose, redraft
from banks.jobs import run_due_jobs, run_job
from banks.packets import DecisionPacket
from banks.reactions import EMOJI_TO_ACTION, draft_ref_of, poll_once
from banks.relay import intent_channel
from banks.store import init_db, cursor


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _pkt(kind="inquiry_reply"):
    return DecisionPacket(kind=kind, decision="d", recommendation="r",
                          default_if_unanswered="hold")


# --- #1 reaction fallback ---------------------------------------------------

class _FakeWeb:
    """Minimal Slack web stub returning one Banks draft message with a reaction."""
    def __init__(self, draft_ref, emoji):
        self._ref, self._emoji = draft_ref, emoji

    def conversations_history(self, channel, limit=50):
        return {"messages": [{
            "blocks": [{"type": "actions", "block_id": f"approve::{self._ref}",
                        "elements": []}],
            "reactions": [{"name": self._emoji, "users": ["U-josh"]}],
        }]}


def test_emoji_vocab_maps_to_all_four_actions():
    assert set(EMOJI_TO_ACTION.values()) == set(ButtonAction)


def test_draft_ref_extracted_from_block_id():
    msg = {"blocks": [{"block_id": "approve::7"}]}
    assert draft_ref_of(msg) == "7"


def test_reaction_poll_applies_approval(db):
    res = propose(db, _pkt(), Draft(kind="inquiry_reply", to="p", subject="s",
                  body="b"), FakeChatPort(), send_channel="email:praise")
    applied = poll_once(db, _FakeWeb(res.draft_ref, "white_check_mark"), "C1")
    assert applied and applied[0][1] is ButtonAction.APPROVE
    with cursor(db) as cur:
        row = cur.execute("SELECT answered_at FROM decision_packets WHERE id=?",
                          (res.packet_id,)).fetchone()
    assert row["answered_at"] is not None


# --- #3 redraft -------------------------------------------------------------

def test_redraft_reposts_and_repends_intent(db):
    chat = FakeChatPort()
    res = propose(db, _pkt(), Draft(kind="inquiry_reply", to="p", subject="v1",
                  body="old"), chat, send_channel="email:praise")
    r2 = redraft(db, res.packet_id, Draft(kind="inquiry_reply", to="p",
                 subject="v2", body="corrected"), chat, send_channel="email:praise")
    assert r2.packet_id == res.packet_id           # same decision
    assert len(chat.posts) == 2                     # reposted
    with cursor(db) as cur:
        row = cur.execute("SELECT status, body FROM send_intents WHERE draft_ref=?",
                          (res.draft_ref,)).fetchone()
    assert row["status"] == "pending" and row["body"] == "corrected"


# --- #4 scheduler dispatch --------------------------------------------------

def test_run_job_posts_brief(db):
    chat = FakeChatPort()
    assert run_job("morning_dashboard", db, chat) is not None
    assert len(chat.posts) == 1


def test_run_due_jobs_fires_morning_dashboard_at_0730(db):
    chat = FakeChatPort()
    # 7:30 America/New_York == 11:30 UTC (EDT, summer)
    now = datetime.fromisoformat("2026-08-05T11:30:00+00:00")
    ran = run_due_jobs(now, db, chat)
    assert "morning_dashboard" in ran


# --- #5 financial → email ---------------------------------------------------

def test_financial_draft_routes_full_body_to_email_not_slack(db):
    chat = FakeChatPort()
    d = Draft(kind="capital", to="you", subject="model", body="LTV 65% IRR 22%",
              detailed_financial=True)
    res = propose(db, _pkt("capital"), d, chat, josh_email="josh@example.com")
    # intent is outbound to Josh with FULL numbers
    assert intent_channel(db, res.draft_ref) == "email:sendas"
    with cursor(db) as cur:
        row = cur.execute("SELECT to_addr, body FROM send_intents WHERE draft_ref=?",
                          (res.draft_ref,)).fetchone()
    assert row["to_addr"] == "josh@example.com"
    assert "IRR 22%" in row["body"]
    # but Slack blocks never carry the numbers
    slack_text = " ".join(str(b) for b in chat.posts[0]["blocks"])
    assert "IRR 22%" not in slack_text
