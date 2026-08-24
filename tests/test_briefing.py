"""Morning brief ordering — B-D1 failure-mode-first."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from banks.briefing import brief_sections
from banks.packets import DecisionPacket, create_packet, mark_answered
from banks.store import init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def test_approved_but_unsent_leads_the_brief(db):
    pid = create_packet(db, DecisionPacket(
        kind="inquiry_reply", decision="Reply to Praise", recommendation="yes",
        default_if_unanswered="hold", dollar_impact_cents=5000))
    mark_answered(db, pid)  # answered, not completed -> should surface, first
    sections = brief_sections(db)
    assert sections[0][0] == "Approved but not sent"
    assert any("Reply to Praise" in line for line in sections[0][1])


def test_no_aging_shows_clean_line_first(db):
    sections = brief_sections(db)
    assert sections[0][0] == "Approved but not sent"
    assert "Nothing approved-but-unsent" in sections[0][1][0]


def test_section_order_is_fixed(db):
    titles = [t for t, _ in brief_sections(db)]
    assert titles == [
        "Approved but not sent",
        "Today's top 1-3",
        "Vacancy",
        "Money due (7-day)",
        "Collections",
        "Deadline radar",
        "Yesterday",
        "Today's schedule",
        "ROI this week",
        "Daily Find",
        "Today's scorecard",
        "Market brief",
    ]
