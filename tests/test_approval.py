"""Block Kit approval logic — pure, no Slack needed (E-D3 / A-D8 as buttons)."""

from __future__ import annotations

import pytest

from banks.approval import (
    ButtonAction,
    apply_action,
    render_draft_blocks,
)
from banks.enforcement import Draft
from banks.packets import DecisionPacket, create_packet
from banks.store import init_db, cursor


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _packet(db) -> int:
    return create_packet(
        db,
        DecisionPacket(
            kind="inquiry_reply",
            decision="Send reply to Praise?",
            recommendation="Yes",
            default_if_unanswered="hold",
        ),
    )


def test_render_has_four_buttons_carrying_draft_ref():
    d = Draft(kind="inquiry_reply", to="praise@x", subject="New inquiry", body="hi")
    blocks = render_draft_blocks(d, draft_ref="42")
    actions = [b for b in blocks if b["type"] == "actions"][0]
    ids = {e["action_id"] for e in actions["elements"]}
    assert ids == {a.value for a in ButtonAction}
    assert all(e["value"] == "42" for e in actions["elements"])
    # no button carries a null style (Slack rejects it)
    assert all("style" not in e or e["style"] in ("primary", "danger")
               for e in actions["elements"])


def test_financial_detail_withheld_from_blocks():
    d = Draft(kind="capital", to="josh", subject="model", body="SECRET NUMBERS",
              detailed_financial=True)
    text = " ".join(str(b) for b in render_draft_blocks(d, "1"))
    assert "SECRET NUMBERS" not in text
    assert "not posted here" in text


def test_approve_answers_but_does_not_complete(db):
    pid = _packet(db)
    res = apply_action(db, ButtonAction.APPROVE, str(pid), "U1", is_outbound=True)
    assert res.enqueue_send is True
    with cursor(db) as cur:
        row = cur.execute(
            "SELECT answered_at, completed_at FROM decision_packets WHERE id=?",
            (pid,)).fetchone()
    assert row["answered_at"] is not None
    assert row["completed_at"] is None  # two-step: answered != completed


def test_approve_internal_does_not_enqueue_send(db):
    pid = _packet(db)
    res = apply_action(db, ButtonAction.APPROVE, str(pid), "U1", is_outbound=False)
    assert res.enqueue_send is False


def test_mark_sent_completes(db):
    pid = _packet(db)
    apply_action(db, ButtonAction.MARK_SENT, str(pid), "U1", is_outbound=True)
    with cursor(db) as cur:
        row = cur.execute(
            "SELECT completed_at FROM decision_packets WHERE id=?", (pid,)).fetchone()
    assert row["completed_at"] is not None


def test_reject_and_revise_never_enqueue_send(db):
    pid = _packet(db)
    for act in (ButtonAction.REJECT, ButtonAction.REVISE):
        res = apply_action(db, act, str(pid), "U1", is_outbound=True)
        assert res.enqueue_send is False
