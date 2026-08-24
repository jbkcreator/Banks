"""ChatPort + propose() flow — draft→packet→post→approve, all via the Fake."""

from __future__ import annotations

import pytest

from banks.approval import ButtonAction, apply_action
from banks.chatport import FakeChatPort
from banks.enforcement import Draft, DraftOnlyViolation, Egress, assert_egress_allowed
from banks.flow import propose
from banks.packets import DecisionPacket
from banks.store import init_db, cursor


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _packet_and_draft():
    return (
        DecisionPacket(kind="inquiry_reply", decision="Reply to Praise?",
                       recommendation="yes", default_if_unanswered="hold",
                       dollar_impact_cents=8500),
        Draft(kind="inquiry_reply", to="praise@x", subject="New inquiry",
              body="room 4 available"),
    )


def test_propose_persists_packet_and_posts_draft_with_matching_ref(db):
    chat = FakeChatPort()
    packet, draft = _packet_and_draft()
    res = propose(db, packet, draft, chat)

    # packet persisted
    with cursor(db) as cur:
        row = cur.execute("SELECT id FROM decision_packets WHERE id=?",
                          (res.packet_id,)).fetchone()
    assert row is not None
    # posted exactly once, buttons carry the same draft_ref as the packet id
    assert len(chat.posts) == 1
    actions = [b for b in chat.posts[0]["blocks"] if b["type"] == "actions"][0]
    assert all(e["value"] == res.draft_ref for e in actions["elements"])
    assert res.draft_ref == str(res.packet_id)


def test_full_loop_propose_then_approve_marks_answered(db):
    chat = FakeChatPort()
    packet, draft = _packet_and_draft()
    res = propose(db, packet, draft, chat)

    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1", is_outbound=True)
    with cursor(db) as cur:
        row = cur.execute("SELECT answered_at, completed_at FROM decision_packets "
                          "WHERE id=?", (res.packet_id,)).fetchone()
    assert row["answered_at"] is not None
    assert row["completed_at"] is None


def test_fake_chatport_still_enforces_drafts_only():
    # The port's egress gate is the drafts-only wall; a forbidden action raises.
    with pytest.raises(DraftOnlyViolation):
        assert_egress_allowed(Egress.SEND_EMAIL)
