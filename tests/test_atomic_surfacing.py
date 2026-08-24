"""Surfacing a draft is atomic — architecture candidate 5.

The invariant: **a decision packet always has a send intent.** If it does not,
Josh can approve a draft that can never send — approve-and-never-send, the
failure he named as his worst case.

Before the refactor `propose()` wrote the two rows in two separate transactions,
so a crash between them broke the invariant. Ordinary tests cannot catch that,
because tests do not crash mid-function — so these tests inject the failure.
"""

from __future__ import annotations

import pytest

from banks.chatport import FakeChatPort
from banks.enforcement import Draft
from banks.flow import propose
from banks.packets import DecisionPacket
from banks.refs import SendChannel
from banks.store import cursor, init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _draft():
    return Draft(kind="vacancy_relist", to="praise@example.com",
                 subject="Room 3 vacant", body="List it today.")


def _packet():
    return DecisionPacket(kind="vacancy_relist", decision="List Room 3?",
                          recommendation="List at $900/mo",
                          default_if_unanswered="list at last rate",
                          dollar_impact_cents=90000)


def _counts(db_path):
    with cursor(db_path) as cur:
        packets = cur.execute("SELECT COUNT(*) AS n FROM decision_packets").fetchone()["n"]
        intents = cur.execute("SELECT COUNT(*) AS n FROM send_intents").fetchone()["n"]
    return packets, intents


def test_happy_path_writes_both_rows(db):
    res = propose(db, _packet(), _draft(), FakeChatPort(), send_channel=SendChannel.PRAISE)
    assert _counts(db) == (1, 1)
    assert res.ref.packet_id == 1


def test_intent_failure_rolls_back_the_packet(db, monkeypatch):
    """The core guarantee: no orphan packet if the intent write fails."""
    import banks.flow as flow

    def boom(*a, **kw):
        raise RuntimeError("simulated crash between the two writes")

    monkeypatch.setattr(flow, "enqueue_intent", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        propose(db, _packet(), _draft(), FakeChatPort(), send_channel=SendChannel.PRAISE)

    # Neither row survives — previously the packet would have been committed.
    assert _counts(db) == (0, 0)


def test_a_bad_channel_surfaces_nothing_at_all(db):
    """A typo'd channel now fails loudly AND leaves no half-written draft."""
    with pytest.raises(ValueError, match="unknown send_channel"):
        propose(db, _packet(), _draft(), FakeChatPort(), send_channel="email:pariase")
    assert _counts(db) == (0, 0)


def test_chat_failure_does_not_roll_back_a_persisted_decision(db):
    """Posting is outside the transaction, deliberately.

    A Slack outage must not discard a decision Banks already made — the rows
    persist so the draft can be re-posted, rather than silently vanishing.
    """
    class BrokenChat(FakeChatPort):
        def post_draft(self, draft, draft_ref):
            raise RuntimeError("slack down")

    with pytest.raises(RuntimeError, match="slack down"):
        propose(db, _packet(), _draft(), BrokenChat(), send_channel=SendChannel.PRAISE)

    # Both rows are there, and the invariant still holds.
    assert _counts(db) == (1, 1)


def test_every_packet_has_an_intent_after_all_six_surfacings(db):
    """Invariant check across the real surfacing paths."""
    from banks.rentals import (mark_vacant, surface_occasion, surface_review_request,
                               surface_maintenance)
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO rooms (property_address, unit_label, rented_by_room, "
            "current_rent_cents, occupied, updated_at) "
            "VALUES ('123 Main St', 'Room 3', 1, 90000, 1, '2026-08-01T00:00:00')"
        )
    chat = FakeChatPort()
    mark_vacant(db, 1)
    from banks.rentals import surface_vacancy
    surface_vacancy(db, 1, chat)
    surface_maintenance(db, 1, "Joe's Plumbing", "123 Main St", "leak", chat)
    surface_review_request(db, "t@example.com", "123 Main St", chat,
                           trigger="smooth_move_in")
    surface_occasion(db, "Mom's birthday", "mom@example.com", chat)

    with cursor(db) as cur:
        orphans = cur.execute(
            "SELECT p.id FROM decision_packets p "
            "LEFT JOIN send_intents i ON i.draft_ref = CAST(p.id AS TEXT) "
            "WHERE i.draft_ref IS NULL"
        ).fetchall()
    assert orphans == [], f"packets with no send intent: {[r['id'] for r in orphans]}"
