"""Regression tests for PR #9 code review findings.

Findings #2 (approve -> surround pack, partially) and #5 (relay send -> lane/
cadence/funnel) had already been fixed by intervening PR #8 work on this
branch (opportunities.source_packet_id; relay_run's mark_lane_sent/
queue_cadence/record_funnel_event). This file covers what was still open:

  #1 any workspace member can approve when BANKS_APPROVER_USER_ID is unset
  #2 approving an opportunity never actually triggers generate_surround_pack
     (the source_packet_id link existed but nothing read it)
  #3 approved outbound drafts are never dispatched by the production runtime
  #4 the scheduled daily queue always behaves as if career-facts is empty
"""
from __future__ import annotations

import datetime as dt

import pytest

from banks.approval import ButtonAction, apply_action
from banks.chatport import FakeChatPort
from banks.config import BanksConfig
from banks.enforcement import Draft
from banks.flow import propose
from banks.jobs import run_job
from banks.mailer import FakeMailer
from banks.opportunity import record_opportunity
from banks.packets import DecisionPacket
from banks.scheduler import due_jobs
from banks.socket_listener import _handle_action, run as listener_run
from banks.store import cursor, init_db

FACTS_MD = """## Identity
GTM leader with 15 years building sales orgs

## Experience
- VP Sales at PropTech Co

## Skills
- enterprise sales
"""


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _write_career_facts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    facts_dir = tmp_path / "banks" / "memory"
    facts_dir.mkdir(parents=True)
    (facts_dir / "career-facts.md").write_text(FACTS_MD, encoding="utf-8")


# --- #1 fail-closed approver lock -------------------------------------------

def _cfg(**overrides):
    base = dict(slack_bot_token="xoxb-test", slack_channel_id="C1",
               slack_app_token="xapp-test", approver_user_id="U-josh")
    base.update(overrides)
    return BanksConfig(**base)


def test_run_rejects_missing_approver_id():
    with pytest.raises(SystemExit, match="BANKS_APPROVER_USER_ID"):
        listener_run(_cfg(approver_user_id=None))


def test_run_rejects_missing_slack_tokens_before_approver_check():
    with pytest.raises(SystemExit, match="BANKS_SLACK_BOT_TOKEN"):
        listener_run(_cfg(slack_bot_token=None, approver_user_id=None))


# --- #2 approve -> surround pack --------------------------------------------

class _FakeWeb:
    def chat_update(self, **_kwargs):
        pass


def _approve_payload(draft_ref, user_id="U-josh"):
    return {
        "actions": [{"action_id": "banks_approve", "value": draft_ref}],
        "user": {"id": user_id},
        "channel": {"id": "C1"},
        "message": {"ts": "1.0"},
    }


def _opportunity_with_packet(db_path, tier):
    opp = record_opportunity(db_path, "VP Sales at Acme", "simplify", 90, tier=tier,
                             company_normalized="acme", industry="PropTech")
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO decision_packets (kind, decision, recommendation, "
            "default_if_unanswered, reversible, created_at) "
            "VALUES ('opportunity', 'Pursue?', 'yes', 'leave in queue', 1, ?)",
            (dt.datetime.now(dt.timezone.utc).isoformat(),),
        )
        packet_id = cur.lastrowid
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, "
            "body, status, created_at) VALUES (?, 'none:internal', '(queued)', "
            "'Tier card', 'body', 'pending', ?)",
            (str(packet_id), dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        cur.execute("UPDATE opportunities SET source_packet_id = ? WHERE id = ?",
                   (packet_id, opp))
    return opp, packet_id


def test_approving_opportunity_card_generates_surround_pack(db, tmp_path, monkeypatch):
    _write_career_facts(tmp_path, monkeypatch)
    opp, packet_id = _opportunity_with_packet(db, "A")
    cfg = _cfg(db_path=db)
    chat = FakeChatPort()

    _handle_action(cfg, _FakeWeb(), _approve_payload(str(packet_id)), llm=None, chat=chat)

    with cursor(db) as cur:
        lanes = cur.execute(
            "SELECT lane_type FROM outreach_lanes WHERE opportunity_id = ?", (opp,)
        ).fetchall()
    lane_types = {r["lane_type"] for r in lanes}
    assert "recruiter" in lane_types
    assert "pov_brief" in lane_types
    assert len(chat.posts) == len(lanes)


def test_approving_opportunity_twice_does_not_duplicate_lanes(db, tmp_path, monkeypatch):
    _write_career_facts(tmp_path, monkeypatch)
    opp, packet_id = _opportunity_with_packet(db, "B")
    cfg = _cfg(db_path=db)
    chat = FakeChatPort()

    payload = _approve_payload(str(packet_id))
    _handle_action(cfg, _FakeWeb(), payload, llm=None, chat=chat)
    _handle_action(cfg, _FakeWeb(), payload, llm=None, chat=chat)

    with cursor(db) as cur:
        n = cur.execute(
            "SELECT COUNT(*) n FROM outreach_lanes WHERE opportunity_id = ?", (opp,)
        ).fetchone()["n"]
    assert n == 1  # Tier B -> recruiter lane only, not duplicated on re-click


# --- #3 relay dispatch job ---------------------------------------------------

def test_relay_dispatch_job_sends_approved_intent(db, monkeypatch):
    res = propose(
        db,
        DecisionPacket(kind="inquiry_reply", decision="Reply to Praise?",
                       recommendation="yes", default_if_unanswered="hold"),
        Draft(kind="inquiry_reply", to="praise@x", subject="New inquiry", body="hi"),
        FakeChatPort(), send_channel="email:praise",
    )
    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")

    fake_mailer = FakeMailer()
    monkeypatch.setattr("banks.mailer.load_mailer", lambda: fake_mailer)

    result = run_job("relay_dispatch", db, FakeChatPort())

    assert result["sent"] == [res.draft_ref]
    assert len(fake_mailer.sent) == 1
    with cursor(db) as cur:
        row = cur.execute("SELECT status FROM send_intents WHERE draft_ref=?",
                          (res.draft_ref,)).fetchone()
    assert row["status"] == "sent"


def test_relay_dispatch_job_tolerates_unconfigured_mailer(db, monkeypatch):
    """No BANKS_SMTP_*/BANKS_RESEND_API_KEY set -> load_mailer() raises;
    the job must no-op (return None) rather than crash the whole tick.

    delenv first: the live server exports these, which would configure a mailer
    and defeat the 'unconfigured' premise — clean env so the test is stable
    regardless of shell state."""
    for var in ("BANKS_SMTP_HOST", "BANKS_SMTP_USER", "BANKS_SMTP_PASSWORD",
                "BANKS_RESEND_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert run_job("relay_dispatch", db, FakeChatPort()) is None


def test_relay_dispatch_is_due_every_five_minutes():
    names_at = lambda now: {j.name for j in due_jobs(now, "UTC")}
    assert "relay_dispatch" in names_at(dt.datetime.fromisoformat("2026-08-28T10:00:00+00:00"))
    assert "relay_dispatch" in names_at(dt.datetime.fromisoformat("2026-08-28T10:05:00+00:00"))
    assert "relay_dispatch" not in names_at(dt.datetime.fromisoformat("2026-08-28T10:03:00+00:00"))


# --- #4 daily queue career-facts --------------------------------------------

def _pending_hm_lane(db_path, opp_id):
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, status, created_at, draft_ref) "
            "VALUES (?, 'hiring_manager', 'pending', '2026-08-28T00:00:00+00:00', '9001')",
            (opp_id,),
        )
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, status, created_at) "
            "VALUES ('9001', 'none:internal', 'x@y.com', 'Reach HM', 'body', 'pending', "
            "'2026-08-28T00:00:00+00:00')"
        )


def test_daily_attack_queue_job_uses_real_career_facts(db, tmp_path, monkeypatch):
    _write_career_facts(tmp_path, monkeypatch)
    opp = record_opportunity(db, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme", industry="PropTech")
    _pending_hm_lane(db, opp)

    chat = FakeChatPort()
    run_job("daily_attack_queue", db, chat)

    summary_text = "\n".join(
        b["text"]["text"] for b in chat.posts[0]["blocks"] if b.get("type") == "section"
    )
    assert "career-facts.md is empty" not in summary_text
    assert "Reach HM" in summary_text
