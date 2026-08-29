"""Relay — approve→send, idempotency, suppression, drafts-only isolation."""

from __future__ import annotations

import pytest

from banks.approval import ButtonAction, apply_action
from banks.chatport import FakeChatPort
from banks.enforcement import Draft
from banks.flow import propose
from banks.mailer import FakeMailer
from banks.packets import DecisionPacket
from banks.relay import relay_run
from banks.store import init_db, cursor


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _outbound(db, chat):
    return propose(
        db,
        DecisionPacket(kind="inquiry_reply", decision="Reply to Praise?",
                       recommendation="yes", default_if_unanswered="hold"),
        Draft(kind="inquiry_reply", to="praise@x", subject="New inquiry", body="hi"),
        chat, send_channel="email:praise",
    )


def test_dispatch_sends_from_configured_smtp_from(db, monkeypatch):
    # Approved outreach must go out FROM Josh's configured address, not the
    # Resend sandbox default (client: outreach from jbkantor@gmail.com).
    from banks.config import BanksConfig
    from banks import relay as relay_mod

    res = _outbound(db, FakeChatPort())
    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")

    cfg = BanksConfig(None, None, smtp_from="jbkantor@gmail.com")
    mailer = FakeMailer()
    monkeypatch.setattr("banks.config.load_config", lambda: cfg)
    monkeypatch.setattr("banks.mailer.load_mailer", lambda config=None: mailer)

    relay_mod.dispatch(db)
    assert mailer.sent and mailer.sent[0]["from"] == "jbkantor@gmail.com"


def test_approve_outbound_then_relay_sends_once(db):
    res = _outbound(db, FakeChatPort())
    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")  # reads intent
    mailer = FakeMailer()
    r1 = relay_run(db, mailer)
    assert r1.sent == [res.draft_ref]
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "praise@x"
    # idempotent: intent is now 'sent', so a second run re-sends nothing
    r2 = relay_run(db, FakeMailer())
    assert r2.sent == []
    # and the UNIQUE receipt guard blocks a re-send even if re-approved
    with cursor(db) as cur:
        cur.execute("UPDATE send_intents SET status='approved' WHERE draft_ref=?",
                    (res.draft_ref,))
    r3 = relay_run(db, FakeMailer())
    assert r3.sent == [] and r3.skipped == [res.draft_ref]


def test_internal_draft_never_enqueues_or_sends(db):
    res = propose(
        db,
        DecisionPacket(kind="bill", decision="Rent due nudge",
                       recommendation="remind", default_if_unanswered="remind"),
        Draft(kind="bill", to="josh", subject="Rent due", body="due in 7d"),
        FakeChatPort(), send_channel="none:internal",
    )
    out = apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")
    assert out.enqueue_send is False
    assert relay_run(db, FakeMailer()).sent == []


def test_mark_sent_suppresses_relay(db):
    res = _outbound(db, FakeChatPort())
    apply_action(db, ButtonAction.MARK_SENT, res.draft_ref, "U1")  # Josh did it
    assert relay_run(db, FakeMailer()).sent == []
    with cursor(db) as cur:
        row = cur.execute("SELECT status FROM send_intents WHERE draft_ref=?",
                          (res.draft_ref,)).fetchone()
    assert row["status"] == "suppressed"


def test_reject_suppresses_relay(db):
    res = _outbound(db, FakeChatPort())
    apply_action(db, ButtonAction.REJECT, res.draft_ref, "U1")
    assert relay_run(db, FakeMailer()).sent == []


def test_relay_records_provider_id(db):
    res = _outbound(db, FakeChatPort())
    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")
    relay_run(db, FakeMailer())
    with cursor(db) as cur:
        row = cur.execute("SELECT status, provider_id FROM sent_receipts "
                          "WHERE draft_ref=?", (res.draft_ref,)).fetchone()
    assert row["status"] == "sent"
    assert row["provider_id"].startswith("fake-")
