from datetime import date, datetime, timezone

from banks.rentals import (
    RateBenchmark,
    advance_maintenance,
    days_vacant,
    inquiry_reply_draft,
    mark_vacant,
    open_maintenance_over,
    rate_memo_draft,
    relisting_draft,
    surface_presented_applicant,
    vendor_draft,
)
from banks.store import cursor


def _seed_room(db_path: str) -> int:
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO rooms (property_address, unit_label, rented_by_room, "
            "current_rent_cents, occupied, updated_at) "
            "VALUES ('123 Main St', 'Room 3', 1, 90000, 1, '2026-08-01T00:00:00')"
        )
        return cur.lastrowid


def test_mark_vacant_starts_days_vacant_clock(db_path):
    room_id = _seed_room(db_path)
    signal = datetime(2026, 8, 1, tzinfo=timezone.utc)

    mark_vacant(db_path, room_id, signal_at=signal)

    d = days_vacant(db_path, room_id, as_of=date(2026, 8, 4))
    assert d == 3


def test_relisting_draft_never_auto_posts():
    draft = relisting_draft("123 Main St", "Room 3", 90000, "Zillow")
    assert "post to Zillow" in draft.to
    assert draft.kind == "relisting_sequence"


def test_surface_presented_applicant_relays_without_screening(db_path):
    # Q13: Banks relays PadSplit's presentation verbatim, never scores.
    from banks.chatport import FakeChatPort
    room_id = _seed_room(db_path)
    chat = FakeChatPort()
    summary = "Verified income 3.2x rent; credit 690; move-in Sept 1 (PadSplit)."
    proposed = surface_presented_applicant(db_path, room_id, "Jane Doe", summary, chat)
    assert proposed is not None
    assert len(chat.posts) == 1


def test_inquiry_reply_drives_to_application_link():
    draft = inquiry_reply_draft("prospect@example.com", "Room 3", "https://apply.example.com")
    assert "https://apply.example.com" in draft.body


def test_maintenance_state_machine_tracks_to_closure(db_path):
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO maintenance_tickets (opened_at, description) "
            "VALUES ('2026-07-20T00:00:00', 'leak')"
        )
        ticket_id = cur.lastrowid

    advance_maintenance(db_path, ticket_id, "vendor_drafted", vendor_name="Joe's Plumbing")
    over_7d = open_maintenance_over(db_path, days=7)
    assert len(over_7d) == 1
    assert over_7d[0]["vendor_name"] == "Joe's Plumbing"

    advance_maintenance(db_path, ticket_id, "closed")
    over_7d_after = open_maintenance_over(db_path, days=7)
    assert len(over_7d_after) == 0


def test_vendor_draft_never_sent_only_drafted():
    draft = vendor_draft("Joe's Plumbing", "123 Main St", "kitchen sink leak")
    assert draft.kind == "vendor_dispatch"


def test_rate_benchmark_recommends_raise_when_under_market():
    benchmark = RateBenchmark(current_rent_cents=90000, comp_rent_cents=105000)
    assert benchmark.recommendation == "raise"
    assert benchmark.gap_cents == 15000

    draft = rate_memo_draft("123 Main St", "Room 3", benchmark)
    assert "raise" in draft.body


def test_rate_benchmark_recommends_hold_when_at_or_above_market():
    benchmark = RateBenchmark(current_rent_cents=110000, comp_rent_cents=105000)
    assert benchmark.recommendation == "hold"
