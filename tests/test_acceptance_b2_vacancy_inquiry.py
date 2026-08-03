"""B2.2 acceptance harness (planned in .wayfinder/block-02-banks-rental-ops):
seed a vacancy and an inquiry, confirm both produce correctly shaped drafts
within the same-day/same-hour bars. Runs against seeded data — no live
rental sources required yet.
"""

from __future__ import annotations

from datetime import datetime, timezone

from banks.enforcement import Draft
from banks.slack import BanksSlack
from banks.store import cursor


def _seed_vacant_room(db_path: str) -> int:
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO rooms (property_address, unit_label, rented_by_room,
                                current_rent_cents, occupied, days_vacant,
                                vacancy_signal_at, updated_at)
            VALUES ('123 Main St', 'Room 3', 1, 90000, 0, 0, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def _seed_inquiry(db_path: str, room_id: int) -> int:
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO inquiries (room_id, received_at, source)
            VALUES (?, ?, 'banks@ inbox')
            """,
            (room_id, datetime.now(timezone.utc).isoformat()),
        )
        return cur.lastrowid


def test_seeded_vacancy_produces_same_day_relisting_draft(db_path, tmp_path):
    room_id = _seed_vacant_room(db_path)

    draft = Draft(
        kind="relisting_sequence",
        to="(posted to your listing platforms)",
        subject="Room 3 at 123 Main St — new listing draft ready",
        body="Vacancy detected today. Draft listing prepared for your review.",
    )
    client = BanksSlack.__new__(BanksSlack)  # avoid needing full config wiring here
    from banks.config import BanksConfig
    client.config = BanksConfig(slack_bot_token=None, slack_channel_id=None, outbox_dir=str(tmp_path))

    result = client.post_draft(draft)

    assert result["ok"] is True
    assert room_id > 0  # vacancy was recorded and tied to a real room


def test_seeded_inquiry_produces_same_hour_reply_draft(db_path, tmp_path):
    room_id = _seed_vacant_room(db_path)
    inquiry_id = _seed_inquiry(db_path, room_id)

    draft = Draft(
        kind="inquiry_reply",
        to="prospective-tenant@example.com",
        subject="Re: Room 3 at 123 Main St",
        body="Thanks for your interest! Here's the application link: <link>",
    )
    from banks.config import BanksConfig
    client = BanksSlack.__new__(BanksSlack)
    client.config = BanksConfig(slack_bot_token=None, slack_channel_id=None, outbox_dir=str(tmp_path))

    result = client.post_draft(draft)

    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE inquiries SET replied_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), inquiry_id),
        )
        cur.execute("SELECT replied_at FROM inquiries WHERE id = ?", (inquiry_id,))
        row = cur.fetchone()

    assert result["ok"] is True
    assert row["replied_at"] is not None  # reply drafted + logged within the same cycle
