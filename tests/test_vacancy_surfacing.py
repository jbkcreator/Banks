"""#2: a detected vacancy self-surfaces a draft via propose()."""

from __future__ import annotations

import pytest

from banks.chatport import FakeChatPort
from banks.rentals import mark_vacant, surface_vacancy
from banks.refs import SendChannel
from banks.relay import intent_channel
from banks.store import init_db, cursor


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    with cursor(path) as cur:
        cur.execute(
            "INSERT INTO rooms (id, property_address, unit_label, rented_by_room, "
            "current_rent_cents, occupied, updated_at) "
            "VALUES (1, '123 Main St', 'Room 4', 1, 85000, 1, '2026-01-01')")
    return path


def test_surface_vacancy_posts_draft_routed_to_praise(db):
    mark_vacant(db, 1)
    chat = FakeChatPort()
    res = surface_vacancy(db, 1, chat)
    # posted to the channel with buttons carrying the packet id
    assert len(chat.posts) == 1
    actions = [b for b in chat.posts[0]["blocks"] if b["type"] == "actions"][0]
    assert all(e["value"] == res.draft_ref for e in actions["elements"])
    # routed to Praise (C-D1), priced at a month's rent
    assert intent_channel(db, res.draft_ref) is SendChannel.PRAISE
    with cursor(db) as cur:
        row = cur.execute("SELECT decision, dollar_impact_cents FROM decision_packets "
                          "WHERE id=?", (res.packet_id,)).fetchone()
    assert "Room 4" in row["decision"]
    assert row["dollar_impact_cents"] == 85000
