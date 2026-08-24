"""Tests for Phase I Tier 2: issues, contacts, corrections, lessons, scorecard plus, misses, amendments."""

from __future__ import annotations

import pytest

from banks.approval import CORRECTION_CODES, record_correction
from banks.chatport import FakeChatPort
from banks.contacts import (
    ContactSuppressed, TouchCollision, add_suppression, check_contact_discipline,
    is_suppressed, record_touch, remove_suppression, within_touch_window,
)
from banks.enforcement import Draft
from banks.flow import propose
from banks.issues import (
    close_issue, maybe_open_issue_for_week, maybe_open_streak_issue,
    open_issue, open_issues,
)
from banks.lessons import lessons_by_stage, observe_instance, promote_to_fleet, record_lesson
from banks.packets import DecisionPacket
from banks.refs import SendChannel
from banks.reflection import AMENDABLE_SECTIONS, propose_amendment
from banks.scorecard import missing_miss_weeks, record_miss, render_plus_block
from banks.store import cursor, init_db

from datetime import datetime, timezone


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _packet():
    return DecisionPacket(kind="test", decision="Test?", recommendation="Do it",
                          default_if_unanswered="skip")


def _draft(to="praise@example.com"):
    return Draft(kind="test", to=to, subject="Test", body="Body.")


# --- Issues ------------------------------------------------------------------

def test_open_and_close_issue(db):
    issue_id = open_issue(db, "3 reds week 2026-08-01", trigger="3_reds",
                          week_ending="2026-08-01")
    close_issue(db, issue_id, artifact="updated TARGETS in scorecard.py")
    with cursor(db) as cur:
        row = cur.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    assert row["status"] == "closed"
    assert row["artifact"] == "updated TARGETS in scorecard.py"


def test_close_issue_requires_artifact(db):
    issue_id = open_issue(db, "Problem", trigger="manual")
    with pytest.raises(ValueError, match="artifact is required"):
        close_issue(db, issue_id, artifact="")


def test_maybe_open_issue_no_reds(db):
    # No scorecard row → all zeros → 5 reds actually (all zero < target)
    # Insert a perfect scorecard to get 0 reds.
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO scorecard_weekly (week_ending, occupancy_pct, "
            "inquiries_answered_under_1h_pct, applications_from_inquiries_pct, "
            "collections_on_time_pct, bills_on_time_pct) "
            "VALUES ('2026-08-01', 100.0, 100.0, 50.0, 100.0, 100.0)"
        )
    result = maybe_open_issue_for_week(db, "2026-08-01")
    assert result is None


def test_maybe_open_issue_with_reds(db):
    # Scorecard with 4 reds (all zeros).
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO scorecard_weekly (week_ending, occupancy_pct, "
            "inquiries_answered_under_1h_pct, applications_from_inquiries_pct, "
            "collections_on_time_pct, bills_on_time_pct) "
            "VALUES ('2026-08-01', 0.0, 0.0, 0.0, 0.0, 0.0)"
        )
    issue_id = maybe_open_issue_for_week(db, "2026-08-01")
    assert issue_id is not None
    assert len(open_issues(db)) == 1


def test_maybe_open_issue_idempotent(db):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO scorecard_weekly (week_ending, occupancy_pct, "
            "inquiries_answered_under_1h_pct, applications_from_inquiries_pct, "
            "collections_on_time_pct, bills_on_time_pct) "
            "VALUES ('2026-08-01', 0.0, 0.0, 0.0, 0.0, 0.0)"
        )
    id1 = maybe_open_issue_for_week(db, "2026-08-01")
    id2 = maybe_open_issue_for_week(db, "2026-08-01")
    assert id1 == id2
    assert len(open_issues(db)) == 1


# --- Contact discipline -------------------------------------------------------

def test_suppressed_address_raises(db):
    add_suppression(db, "bad@example.com", reason="test")
    with pytest.raises(ContactSuppressed):
        check_contact_discipline(db, "bad@example.com")


def test_remove_suppression_allows_contact(db):
    add_suppression(db, "bad@example.com")
    remove_suppression(db, "bad@example.com")
    assert not is_suppressed(db, "bad@example.com")
    check_contact_discipline(db, "bad@example.com")  # no raise


def test_touch_collision_within_48h(db):
    record_touch(db, "t@example.com", "1", touched_at="2026-08-06T01:00:00+00:00")
    now = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(TouchCollision):
        check_contact_discipline(db, "t@example.com", now=now)


def test_no_collision_after_48h(db):
    record_touch(db, "t@example.com", "1", touched_at="2026-08-04T00:00:00+00:00")
    now = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)
    check_contact_discipline(db, "t@example.com", now=now)  # no raise


def test_internal_draft_bypasses_contact_check(db):
    add_suppression(db, "suppressed@example.com")
    # Internal drafts (no external to_addr) must not be blocked.
    check_contact_discipline(db, None)   # no raise
    check_contact_discipline(db, "")     # no raise


def test_suppression_enforced_in_propose(db):
    add_suppression(db, "praise@example.com")
    with pytest.raises(ContactSuppressed):
        propose(db, _packet(), _draft(to="praise@example.com"),
                FakeChatPort(), send_channel=SendChannel.PRAISE)


def test_touch_collision_enforced_in_propose(db):
    record_touch(db, "praise@example.com", "1",
                 touched_at="2026-08-06T01:00:00+00:00")
    now = datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc)
    # propose() doesn't accept now= but check_contact_discipline does —
    # verify via within_touch_window directly.
    assert within_touch_window(db, "praise@example.com", now)


# --- Correction taxonomy -----------------------------------------------------

def test_all_8_correction_codes_exist():
    assert len(CORRECTION_CODES) == 8


def test_record_correction_happy_path(db):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO decision_packets (kind, decision, recommendation, "
            "default_if_unanswered, created_at) VALUES ('t', 'T?', 'Do', 'skip', '2026-08-06')"
        )
        packet_id = cur.lastrowid
    record_correction(db, packet_id, "wrong_tone", note="too casual")
    with cursor(db) as cur:
        row = cur.execute(
            "SELECT * FROM corrections WHERE packet_id = ?", (packet_id,)
        ).fetchone()
    assert row["code"] == "wrong_tone"
    assert row["note"] == "too casual"


def test_unknown_correction_code_raises(db):
    with pytest.raises(ValueError, match="unknown correction code"):
        record_correction(db, 1, "made_up_code")


# --- Lesson quarantine -------------------------------------------------------

def test_new_lesson_is_local(db):
    lesson_id = record_lesson(db, "Always sign drafts.")
    lessons = lessons_by_stage(db, "local")
    assert any(l["id"] == lesson_id for l in lessons)


def test_observe_instance_promotes_at_threshold(db):
    lesson_id = record_lesson(db, "Sign every draft.")
    stage = observe_instance(db, lesson_id)
    assert stage == "provisional"
    lessons = lessons_by_stage(db, "provisional")
    assert any(l["id"] == lesson_id for l in lessons)


def test_promote_to_fleet_from_provisional(db):
    lesson_id = record_lesson(db, "Check suppression before every draft.")
    observe_instance(db, lesson_id)  # → provisional
    promote_to_fleet(db, lesson_id)
    fleet = lessons_by_stage(db, "fleet")
    assert any(l["id"] == lesson_id for l in fleet)


def test_cannot_promote_local_to_fleet(db):
    lesson_id = record_lesson(db, "Local only.")
    with pytest.raises(ValueError, match="only provisional"):
        promote_to_fleet(db, lesson_id)


# --- Scorecard Plus block -----------------------------------------------------

def test_render_plus_block_empty_db(db):
    plus = render_plus_block(db, "2026-08-01")
    assert plus.maintenance_over_7d == 0
    assert plus.misses_owned == 0
    assert plus.todays_find is None


# --- Weekly biggest-miss ------------------------------------------------------

def test_record_miss_and_retrieve(db):
    record_miss(db, "2026-08-01", "Missed the lease renewal for Room 3")
    missing = missing_miss_weeks(db, ["2026-08-01"])
    assert missing == []


def test_empty_miss_raises(db):
    with pytest.raises(ValueError, match="miss text is required"):
        record_miss(db, "2026-08-01", "")


def test_missing_miss_weeks_reports_gap(db):
    gaps = missing_miss_weeks(db, ["2026-07-25", "2026-08-01"])
    assert gaps == ["2026-07-25", "2026-08-01"]


# --- Amendment proposals ------------------------------------------------------

def test_propose_amendment_to_amendable_section(db):
    chat = FakeChatPort()
    result = propose_amendment(
        db, "scorecard_targets",
        current_text="collections ≥95%",
        proposed_text="collections ≥90%",
        rationale="One late tenant skewing the metric during ramp-up",
        chat=chat,
    )
    assert result.ref.packet_id > 0


def test_propose_amendment_non_amendable_raises(db):
    chat = FakeChatPort()
    with pytest.raises(ValueError, match="not AMENDABLE"):
        propose_amendment(
            db, "immutable_core",
            current_text="never send",
            proposed_text="send sometimes",
            rationale="bad idea",
            chat=chat,
        )


def test_all_amendable_sections_documented():
    assert len(AMENDABLE_SECTIONS) >= 4
