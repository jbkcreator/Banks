"""Tests for Phase I Tier 1: collections, Daily Find, deadline radar, sign()."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from banks.briefing import brief_sections, _deadline_radar_lines
from banks.chatport import FakeChatPort
from banks.collections import (
    collections_on_time_pct, overdue_charges, record_charge, record_payment,
    surface_overdue_nudges,
)
from banks.enforcement import sign
from banks.find import DailyFind, find_brief_lines, get_find, record_find
from banks.store import cursor, init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _room(db):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO rooms (property_address, unit_label, rented_by_room, "
            "current_rent_cents, occupied, updated_at) "
            "VALUES ('123 Main', 'Room 1', 1, 90000, 1, '2026-01-01')"
        )
        return cur.lastrowid


# --- sign() ------------------------------------------------------------------

def test_sign_appends_signature():
    assert sign("Hello Josh.").endswith("— Banks.")


def test_sign_idempotent():
    signed = sign("Hello Josh.")
    assert sign(signed) == signed


def test_sign_strips_trailing_whitespace_before_signing():
    result = sign("Hello Josh.   ")
    assert result == "Hello Josh.\n\n— Banks."


# --- Daily Find --------------------------------------------------------------

def test_get_find_returns_none_when_not_recorded(db):
    assert get_find(db, "2026-08-06") is None


def test_record_and_retrieve_find(db):
    record_find(db, "article", title="Why co-living works",
                url="https://example.com", summary="Short summary", date="2026-08-06")
    f = get_find(db, "2026-08-06")
    assert isinstance(f, DailyFind)
    assert f.kind == "article"
    assert f.title == "Why co-living works"


def test_find_kind_none_is_honest_empty(db):
    record_find(db, "none", date="2026-08-06")
    lines = find_brief_lines(db, "2026-08-06")
    assert lines == ["— (nothing surfaced today)"]


def test_find_brief_lines_when_article(db):
    record_find(db, "tip", title="Tip: Use PadSplit analytics",
                summary="Weekly funnel data", date="2026-08-06")
    lines = find_brief_lines(db, "2026-08-06")
    assert any("TIP" in l for l in lines)
    assert any("Tip: Use PadSplit analytics" in l for l in lines)


def test_unknown_find_kind_raises(db):
    with pytest.raises(ValueError, match="unknown find kind"):
        record_find(db, "video", date="2026-08-06")


def test_find_replaces_on_same_date(db):
    record_find(db, "article", title="First", date="2026-08-06")
    record_find(db, "tip", title="Second", date="2026-08-06")
    f = get_find(db, "2026-08-06")
    assert f.kind == "tip"
    assert f.title == "Second"


# --- Collections -------------------------------------------------------------

def test_record_charge_and_payment(db):
    room_id = _room(db)
    charge_id = record_charge(db, room_id, "2026-08-01", "2026-08-31", 90000, "2026-08-05")
    record_payment(db, room_id, charge_id, 90000)
    with cursor(db) as cur:
        row = cur.execute(
            "SELECT status FROM rent_charges WHERE id = ?", (charge_id,)
        ).fetchone()
    assert row["status"] == "paid"


def test_overdue_charges_returns_past_due(db):
    room_id = _room(db)
    record_charge(db, room_id, "2026-07-01", "2026-07-31", 90000, "2026-07-05")
    overdue = overdue_charges(db, as_of="2026-08-06")
    assert len(overdue) == 1
    assert overdue[0]["amount_cents"] == 90000


def test_overdue_charges_excludes_paid(db):
    room_id = _room(db)
    charge_id = record_charge(db, room_id, "2026-07-01", "2026-07-31", 90000, "2026-07-05")
    record_payment(db, room_id, charge_id, 90000)
    assert overdue_charges(db, as_of="2026-08-06") == []


def test_collections_on_time_pct_all_paid_on_time(db):
    room_id = _room(db)
    charge_id = record_charge(db, room_id, "2026-08-01", "2026-08-31", 90000, "2026-08-05")
    record_payment(db, room_id, charge_id, 90000, paid_at="2026-08-04T10:00:00")
    pct = collections_on_time_pct(db, "2026-08-01", "2026-08-31")
    assert pct == 100.0


def test_collections_on_time_pct_returns_none_when_no_charges(db):
    assert collections_on_time_pct(db, "2026-08-01", "2026-08-31") is None


def test_surface_overdue_nudges_drafts_for_each_overdue(db):
    room_id = _room(db)
    record_charge(db, room_id, "2026-07-01", "2026-07-31", 90000, "2026-07-05")
    chat = FakeChatPort()
    results = surface_overdue_nudges(db, chat, as_of="2026-08-06")
    assert len(results) == 1
    assert results[0].ref.packet_id > 0


def test_surface_overdue_nudges_idempotent(db):
    room_id = _room(db)
    record_charge(db, room_id, "2026-07-01", "2026-07-31", 90000, "2026-07-05")
    chat = FakeChatPort()
    surface_overdue_nudges(db, chat, as_of="2026-08-06")
    second = surface_overdue_nudges(db, chat, as_of="2026-08-06")
    assert second == []  # already nudged, no duplicate


# --- Deadline radar ----------------------------------------------------------

def test_deadline_radar_empty_db_shows_nothing(db):
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    lines = _deadline_radar_lines(db, now)
    assert lines == ["Nothing on the radar in the next 7 days."]


def test_deadline_radar_surfaces_upcoming_decision(db):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO decision_packets (kind, decision, recommendation, "
            "default_if_unanswered, deadline, created_at) "
            "VALUES ('test', 'Sign lease for Room 3?', 'Sign it', 'defer', "
            "'2026-08-08T00:00:00', '2026-08-06T00:00:00')"
        )
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    lines = _deadline_radar_lines(db, now)
    assert any("Sign lease" in l for l in lines)


def test_deadline_radar_ignores_answered_decisions(db):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO decision_packets (kind, decision, recommendation, "
            "default_if_unanswered, deadline, answered_at, created_at) "
            "VALUES ('test', 'Already answered?', 'Yes', 'defer', "
            "'2026-08-08T00:00:00', '2026-08-06T00:00:00', '2026-08-06T00:00:00')"
        )
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    lines = _deadline_radar_lines(db, now)
    assert lines == ["Nothing on the radar in the next 7 days."]


def test_deadline_radar_surfaces_upcoming_lease_end(db):
    room_id = _room(db)
    with cursor(db) as cur:
        cur.execute(
            "UPDATE rooms SET lease_end = '2026-08-20' WHERE id = ?", (room_id,)
        )
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    lines = _deadline_radar_lines(db, now)
    assert any("Lease ending" in l for l in lines)


# --- Brief integration -------------------------------------------------------

def test_brief_sections_includes_all_tier1_sections(db):
    titles = [t for t, _ in brief_sections(db)]
    for expected in ("Collections", "Deadline radar", "Yesterday",
                     "Today's schedule", "Daily Find", "Today's scorecard"):
        assert expected in titles, f"missing section: {expected}"


def test_collections_section_shows_current_when_empty(db):
    sections = dict(brief_sections(db))
    assert sections["Collections"] == ["✓ All rent current."]


def test_daily_find_section_shows_not_recorded_when_missing(db):
    sections = dict(brief_sections(db))
    assert sections["Daily Find"] == ["No find recorded yet today."]
