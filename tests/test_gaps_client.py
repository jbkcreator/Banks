"""Tests for client-answer gaps: bill category, ROI $48, review triggers,
brief staleness, listing formats, receipt folder routing, Q13 no-screening."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from banks.store import cursor, init_db


@pytest.fixture()
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


# ── #1 bill categorization (Q19) ─────────────────────────────────────────────

def test_bill_category_defaults_from_property(db):
    from banks.finance import upsert_bill_from_extract
    prop_id = upsert_bill_from_extract(db, {
        "name": "Water", "amount_cents": 4000, "due_date": "2026-09-01",
        "property_address": "123 Main St",
    })
    pers_id = upsert_bill_from_extract(db, {
        "name": "Netflix", "amount_cents": 1500, "due_date": "2026-09-05",
        "property_address": None,
    })
    with cursor(db) as cur:
        cur.execute("SELECT bill_category FROM bills WHERE id = ?", (prop_id,))
        assert cur.fetchone()["bill_category"] == "property"
        cur.execute("SELECT bill_category FROM bills WHERE id = ?", (pers_id,))
        assert cur.fetchone()["bill_category"] == "personal"


def test_expenses_roll_up_per_property(db):
    from banks.finance import expenses_by_property, personal_expenses_total_cents, upsert_bill_from_extract
    upsert_bill_from_extract(db, {"name": "Water", "amount_cents": 4000,
                                  "due_date": "2026-09-01", "property_address": "123 Main St"})
    upsert_bill_from_extract(db, {"name": "Power", "amount_cents": 6000,
                                  "due_date": "2026-09-02", "property_address": "123 Main St"})
    upsert_bill_from_extract(db, {"name": "Netflix", "amount_cents": 1500,
                                  "due_date": "2026-09-05", "property_address": None})
    rollup = expenses_by_property(db)
    assert len(rollup) == 1
    assert rollup[0]["property_address"] == "123 Main St"
    assert rollup[0]["total_cents"] == 10000
    assert personal_expenses_total_cents(db) == 1500


# ── #2 ROI $48/hr (Q24) ──────────────────────────────────────────────────────

def test_hourly_value_is_48_dollars():
    from banks.schedule import HOURLY_VALUE_CENTS
    assert HOURLY_VALUE_CENTS == 4800


def test_roi_uses_48_dollar_rate():
    from banks.schedule import OpportunityCostInputs, weekly_roi
    roi = weekly_roi(OpportunityCostInputs(hours_saved=10.0))
    assert roi["value_returned_cents"] == 48000  # 10h * $48


def test_briefing_roi_shows_dollars(db):
    from banks.activity_log import log_event
    from banks.briefing import brief_sections
    log_event(db, "draft_created", minutes_saved=600.0)  # 10h
    sections = dict(brief_sections(db))
    roi_line = sections["ROI this week"][0]
    assert "$480" in roi_line


# ── #3 review triggers (Q16) ─────────────────────────────────────────────────

def test_review_fires_only_on_approved_triggers():
    from banks.rentals import should_request_review
    assert should_request_review("maintenance_resolved_promptly")
    assert should_request_review("smooth_move_in")
    assert should_request_review("unprompted_appreciation")
    assert not should_request_review("random_event")


def test_payment_streak_off_by_default():
    from banks.rentals import should_request_review
    assert not should_request_review("payment_streak")
    assert should_request_review("payment_streak", payment_streak_enabled=True)


def test_surface_review_returns_none_on_bad_trigger(db):
    from banks.chatport import FakeChatPort
    from banks.rentals import surface_review_request
    result = surface_review_request(db, "t@ex.com", "123 Main", FakeChatPort(),
                                    trigger="not_a_trigger")
    assert result is None


# ── #4 market-brief staleness (Q7) ───────────────────────────────────────────

def test_brief_fresh_when_recorded_today(db):
    from banks.briefport import brief_status, record_daily_brief
    record_daily_brief(db, "Rates up 5bps. Inventory tight.")
    status = brief_status(db)
    assert status.present and not status.stale
    assert "Rates up" in status.text


def test_brief_stale_after_two_days(db):
    from banks.briefport import brief_status, record_daily_brief
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    record_daily_brief(db, "Old brief", now=two_days_ago)
    status = brief_status(db)
    assert status.present and status.stale
    assert status.text is None  # not served when stale (Q7 graceful degrade)


def test_brief_section_flags_stale(db):
    from banks.briefport import brief_section_lines, record_daily_brief
    old = datetime.now(timezone.utc) - timedelta(days=3)
    record_daily_brief(db, "stale brief", now=old)
    lines = brief_section_lines(db)
    assert "stale" in lines[0].lower()


# ── #5 extensible listing formats (Q10) ──────────────────────────────────────

def test_listing_formats_differ_per_platform():
    from banks.rentals import relisting_draft
    padsplit = relisting_draft("123 Main", "Room 1", 90000, "PadSplit")
    roomi = relisting_draft("123 Main", "Room 1", 90000, "Roomi")
    assert padsplit.body != roomi.body
    assert "PadSplit" in padsplit.body or "co-living" in padsplit.body


def test_register_new_listing_format():
    from banks.rentals import register_listing_format, relisting_draft
    register_listing_format("Craigslist", lambda a, u, r: f"CL: {u} ${r:.0f}")
    draft = relisting_draft("123 Main", "Room 1", 90000, "Craigslist")
    assert "CL: Room 1" in draft.body


def test_unknown_platform_uses_generic_format():
    from banks.rentals import relisting_draft
    draft = relisting_draft("123 Main", "Room 1", 90000, "Zillow")
    assert "post to Zillow" in draft.to
    assert draft.body  # generic body, no crash


# ── #6 receipt folder routing (Q20) ──────────────────────────────────────────

def test_resolve_receipt_folder_per_property():
    from banks.fileport import resolve_receipt_folder
    fmap = {"123 Main St": "folder_main", "456 Oak": "folder_oak"}
    assert resolve_receipt_folder("123 Main St", fmap, "personal", "default") == "folder_main"
    assert resolve_receipt_folder(None, fmap, "personal", "default") == "personal"
    assert resolve_receipt_folder("999 Unknown", fmap, "personal", "default") == "default"


def test_receipt_routes_to_property_folder():
    from banks.fileport import FakeFilePort, file_receipt_from_eml
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort()
    llm.register("plumbing", '{"vendor":"Bob","amount_cents":25000,"date":"2026-08-04",'
                             '"property_address":"123 Main St","description":"repair"}')
    fp = FakeFilePort()
    eml = "Subject: plumbing receipt\n\nPlumbing repair at 123 Main St"
    file_receipt_from_eml(eml, llm, fp, folder_map={"123 Main St": "folder_main"},
                          personal_folder_id="personal")
    assert fp.uploads[0]["parent_folder_id"] == "folder_main"


# ── #7 Q13: no independent screening ─────────────────────────────────────────

def test_no_score_inquiry_function():
    import banks.rentals as r
    assert not hasattr(r, "score_inquiry")
    assert not hasattr(r, "ApplicantCriteria")
    assert not hasattr(r, "InquiryFacts")


def test_surface_presented_applicant_relays(db):
    from banks.chatport import FakeChatPort
    from banks.rentals import surface_presented_applicant
    with cursor(db) as cur:
        cur.execute("INSERT INTO rooms (property_address, unit_label, rented_by_room, "
                    "current_rent_cents, occupied, updated_at) "
                    "VALUES ('123 Main', 'Room 1', 1, 90000, 0, '2026-01-01')")
        room_id = cur.lastrowid
    chat = FakeChatPort()
    proposed = surface_presented_applicant(db, room_id, "Jane", "PadSplit summary here", chat)
    assert proposed is not None
    assert len(chat.posts) == 1
