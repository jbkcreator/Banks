"""MOD-05 queue actions — snooze / skip / mark-done + carry-over helpers."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.attack_queue import build_sections, post_daily_queue
from banks.chatport import FakeChatPort
from banks.opportunity import CareerFacts, record_opportunity
from banks.queue_actions import (
    carried_over_items,
    due_snoozed_items,
    mark_done,
    skip_item,
    snooze_item,
)
from banks.store import cursor, init_db

FACTS = CareerFacts(identity="GTM leader", experience=("VP Sales",), skills=("sales",))


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _pending_lane(db_path, opp_id, lane_type="hiring_manager", contact_id=None):
    now = _now().isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, contact_id, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (opp_id, lane_type, contact_id, now),
        )
        lane_id = cur.lastrowid
        ref = str(1000 + lane_id)
        cur.execute("UPDATE outreach_lanes SET draft_ref = ? WHERE id = ?", (ref, lane_id))
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, status, created_at) "
            "VALUES (?, 'none:internal', 'x@y.com', 'Hi', 'body', 'pending', ?)",
            (ref, now),
        )
    return ref, lane_id


def test_snooze_hides_then_reappears(db_path):
    opp = record_opportunity(db_path, "VP", "simplify", 90, tier="A", company_normalized="a")
    ref, _ = _pending_lane(db_path, opp)
    # surface it yesterday so it can carry over
    yesterday = (_now() - dt.timedelta(days=1)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO queue_items (draft_ref, category, state, first_surfaced_at, last_surfaced_at) "
            "VALUES (?, 'tier_a', 'active', ?, ?)",
            (ref, yesterday, yesterday),
        )
    snooze_item(db_path, ref, days=1)
    today = _now().date().isoformat()
    # snoozed until tomorrow → not in today's carried-over
    carried_today = [c["draft_ref"] for c in build_sections(db_path, now=_now(), career_facts=FACTS)
                     if c.title.startswith("⚠️") for c in c.cards]
    assert ref not in carried_today
    # two days later → snooze_until has passed → due again
    later = _now() + dt.timedelta(days=2)
    due = due_snoozed_items(db_path, later.date().isoformat())
    assert any(d["draft_ref"] == ref for d in due)


def test_skip_no_resurface(db_path):
    opp = record_opportunity(db_path, "VP", "simplify", 90, tier="A", company_normalized="a")
    ref, _ = _pending_lane(db_path, opp)
    yesterday = (_now() - dt.timedelta(days=1)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO queue_items (draft_ref, category, state, first_surfaced_at, last_surfaced_at) "
            "VALUES (?, 'tier_a', 'active', ?, ?)",
            (ref, yesterday, yesterday),
        )
    skip_item(db_path, ref)
    # not carried over, not snooze-due
    assert not carried_over_items(db_path, _now().date().isoformat())
    assert not due_snoozed_items(db_path, (_now() + dt.timedelta(days=30)).date().isoformat())


def test_mark_done_feeds_cadence_funnel_touchlog(db_path):
    opp = record_opportunity(db_path, "VP", "simplify", 90, tier="A", company_normalized="a")
    ref, lane_id = _pending_lane(db_path, opp)
    mark_done(db_path, ref)
    with cursor(db_path) as cur:
        lane = cur.execute("SELECT sent_at, status FROM outreach_lanes WHERE id = ?",
                           (lane_id,)).fetchone()
        cadence = cur.execute("SELECT COUNT(*) n FROM cadence_queue WHERE outreach_lane_id = ?",
                              (lane_id,)).fetchone()["n"]
        funnel = cur.execute("SELECT COUNT(*) n FROM funnel_events WHERE opportunity_id = ? "
                             "AND event_type = 'contacted'", (opp,)).fetchone()["n"]
        touch = cur.execute("SELECT COUNT(*) n FROM touch_log WHERE draft_ref = ?",
                            (ref,)).fetchone()["n"]
        qi = cur.execute("SELECT state FROM queue_items WHERE draft_ref = ?", (ref,)).fetchone()
        intent = cur.execute("SELECT status FROM send_intents WHERE draft_ref = ?",
                             (ref,)).fetchone()
    assert lane["sent_at"] and lane["status"] == "sent"
    assert cadence == 3                    # Day 3/7/14 queued
    assert funnel == 1
    assert touch == 1
    assert qi["state"] == "done"
    assert intent["status"] == "suppressed"  # Relay must never fire on a manual action


def test_done_item_drops_from_queue(db_path):
    opp = record_opportunity(db_path, "VP", "simplify", 90, tier="A", company_normalized="a")
    ref, _ = _pending_lane(db_path, opp)
    post_daily_queue(db_path, FakeChatPort(), now=_now(), career_facts=FACTS)
    mark_done(db_path, ref)
    # next day: the done item's intent is suppressed → not pending → not carried over
    tomorrow = _now() + dt.timedelta(days=1)
    carried = carried_over_items(db_path, tomorrow.date().isoformat())
    # queue_items row is 'done', so carried_over (which filters state='active') excludes it
    assert not any(c["draft_ref"] == ref for c in carried)
