"""MOD-04 Governance tests: daily caps, collision protection, cadence, funnel."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.governance import (
    DAILY_CAPS,
    check_and_increment,
    check_14day_spacing,
    due_cadence_touches,
    freeze_company,
    got_reply,
    is_company_frozen,
    mark_lane_sent,
    network_activation_due,
    pending_secondary_escalations,
    queue_cadence,
    record_funnel_event,
    record_interview,
    record_offer,
    weekly_funnel_summary,
)
from banks.opportunity import record_opportunity
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _opp(db_path, company="acme"):
    return record_opportunity(
        db_path, "VP Sales", "simplify", 80,
        tier="A", company_normalized=company, industry="PropTech",
    )


def _lane(db_path, opp_id):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, created_at) "
            "VALUES (?, 'hiring_manager', ?)",
            (opp_id, now),
        )
        return cur.lastrowid


# --- daily caps ---

def test_first_call_under_cap(db_path):
    assert check_and_increment(db_path, "email", "2026-08-25") is True


def test_linkedin_cap_enforced(db_path):
    cap = DAILY_CAPS["linkedin"]
    for _ in range(cap):
        check_and_increment(db_path, "linkedin", "2026-08-25")
    assert check_and_increment(db_path, "linkedin", "2026-08-25") is False


def test_email_cap_enforced(db_path):
    cap = DAILY_CAPS["email"]
    for _ in range(cap):
        check_and_increment(db_path, "email", "2026-08-26")
    assert check_and_increment(db_path, "email", "2026-08-26") is False


def test_cap_resets_next_day(db_path):
    cap = DAILY_CAPS["linkedin"]
    for _ in range(cap):
        check_and_increment(db_path, "linkedin", "2026-08-25")
    # Next day should be under cap again
    assert check_and_increment(db_path, "linkedin", "2026-08-26") is True


def test_unknown_channel_uncapped(db_path):
    for _ in range(1000):
        result = check_and_increment(db_path, "fax", "2026-08-25")
    assert result is True


# --- company freeze ---

def test_no_freeze_initially(db_path):
    assert is_company_frozen(db_path, "acme") is False


def test_freeze_company(db_path):
    freeze_company(db_path, "acme", reason="got_reply")
    assert is_company_frozen(db_path, "acme") is True


def test_freeze_with_thaw(db_path):
    freeze_company(db_path, "acme", reason="got_reply", thaw_after_days=-1)
    # Already past thaw — should not be frozen
    assert is_company_frozen(db_path, "acme") is False


def test_freeze_idempotent(db_path):
    freeze_company(db_path, "acme")
    freeze_company(db_path, "acme")  # second call should not error
    assert is_company_frozen(db_path, "acme") is True


# --- got_reply signal ---

def test_got_reply_freezes_company(db_path):
    opp_id = _opp(db_path, "acme")
    got_reply(db_path, opp_id)
    assert is_company_frozen(db_path, "acme") is True


def test_got_reply_logs_funnel_event(db_path):
    opp_id = _opp(db_path, "acme")
    got_reply(db_path, opp_id)
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT event_type FROM funnel_events WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()
    assert row["event_type"] == "replied"


def test_got_reply_freezes_cadence_touches(db_path):
    opp_id = _opp(db_path, "acme")
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    got_reply(db_path, opp_id)
    touches = due_cadence_touches(db_path, "2026-09-10")
    assert all(t["status"] == "frozen" for t in touches)


def test_got_reply_null_company_no_error(db_path):
    # opportunity with no company_normalized
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO opportunities (title, source, criteria_match_score, status, "
            "tier, needs_enrichment) VALUES ('X', 'manual', 50, 'sourced', 'B', 0)"
        )
        opp_id = cur.lastrowid
    got_reply(db_path, opp_id)  # should not raise


# --- cadence queue ---

def test_queue_cadence_creates_3_touches(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    with cursor(db_path) as cur:
        n = cur.execute(
            "SELECT COUNT(*) n FROM cadence_queue WHERE outreach_lane_id = ?", (lane_id,)
        ).fetchone()["n"]
    assert n == 3


def test_cadence_touch_1_due_day_3(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    touches = due_cadence_touches(db_path, "2026-08-28")
    assert len(touches) == 1
    assert touches[0]["touch_number"] == 1


def test_cadence_touch_2_due_day_7(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    touches = due_cadence_touches(db_path, "2026-09-01")
    assert len(touches) == 2  # touch 1 and 2 are both due


def test_cadence_idempotent(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    queue_cadence(db_path, lane_id, "2026-08-25")  # second call — no duplicates
    with cursor(db_path) as cur:
        n = cur.execute(
            "SELECT COUNT(*) n FROM cadence_queue WHERE outreach_lane_id = ?", (lane_id,)
        ).fetchone()["n"]
    assert n == 3


# --- funnel ---

def test_record_and_summarise_funnel(db_path):
    import datetime
    today = datetime.date.today().isoformat()
    opp_id = _opp(db_path)
    record_funnel_event(db_path, opp_id, "applied")
    record_funnel_event(db_path, opp_id, "contacted")
    record_funnel_event(db_path, opp_id, "contacted")
    summary = weekly_funnel_summary(db_path, today)
    assert summary.get("applied", 0) >= 1
    assert summary.get("contacted", 0) >= 2


# --- 14-day spacing ---

def test_14day_spacing_no_prior_touch(db_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, email, degree, source, added_at) "
            "VALUES ('A', 'a@x.com', 1, 'manual', ?)",
            (now,),
        )
        cid = cur.lastrowid
    assert check_14day_spacing(db_path, cid, "2026-08-25") is True


def test_14day_spacing_recent_touch_blocked(db_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, email, degree, source, added_at) "
            "VALUES ('B', 'b@x.com', 1, 'manual', ?)",
            (now,),
        )
        cid = cur.lastrowid
        # Uses outreach_lanes.sent_at (keyed by contact_id) — not touch_log
        cur.execute(
            "INSERT INTO outreach_lanes "
            "(opportunity_id, lane_type, contact_id, status, created_at, sent_at) "
            "VALUES (1, 'hiring_manager', ?, 'sent', ?, ?)",
            (cid, now, now),
        )
    assert check_14day_spacing(db_path, cid, "2026-08-25") is False


# --- new: record_interview / record_offer ---

def test_record_interview(db_path):
    import datetime
    today = datetime.date.today().isoformat()
    opp_id = _opp(db_path)
    record_interview(db_path, opp_id)
    summary = weekly_funnel_summary(db_path, today)
    assert summary.get("interview", 0) >= 1


def test_record_offer(db_path):
    import datetime
    today = datetime.date.today().isoformat()
    opp_id = _opp(db_path)
    record_offer(db_path, opp_id)
    summary = weekly_funnel_summary(db_path, today)
    assert summary.get("offer", 0) >= 1


# --- reply-safety (review #8): a reply freezes the company's cadence ---------

def test_record_reply_freezes_company_and_stops_cadence(db_path):
    from banks.governance import record_reply
    opp_id = _opp(db_path, "acme")
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    # before: 3 touches would come due
    assert len(due_cadence_touches(db_path, "2026-09-30")) == 3

    n = record_reply(db_path, "acme")
    assert n == 1
    assert is_company_frozen(db_path, "acme") is True
    # after: nobody who replied gets chased — zero due touches
    assert due_cadence_touches(db_path, "2026-09-30") == []


def test_replied_command_triggers_freeze(db_path):
    from banks.commands import handle_command, route
    opp_id = _opp(db_path, "acme")
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    cmd = route(db_path, "replied Acme")
    assert cmd.intent == "replied" and cmd.company.lower() == "acme"
    msg = handle_command(db_path, cmd)
    assert "Froze" in msg
    assert is_company_frozen(db_path, "acme") is True
    assert due_cadence_touches(db_path, "2026-09-30") == []


# --- new: cadence stops on interviewing/closed ---

def test_cadence_stops_when_interviewing(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE opportunities SET status = 'interviewing' WHERE id = ?", (opp_id,)
        )
    touches = due_cadence_touches(db_path, "2026-09-10")
    assert len(touches) == 0


def test_cadence_stops_when_closed(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    queue_cadence(db_path, lane_id, "2026-08-25")
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE opportunities SET status = 'closed' WHERE id = ?", (opp_id,)
        )
    touches = due_cadence_touches(db_path, "2026-09-10")
    assert len(touches) == 0


# --- new: queue_cadence reads sent_at from lane ---

def test_queue_cadence_reads_lane_sent_at(db_path):
    opp_id = _opp(db_path)
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes "
            "(opportunity_id, lane_type, created_at, sent_at) "
            "VALUES (?, 'hiring_manager', '2026-08-25T00:00:00', '2026-08-25T00:00:00')",
            (opp_id,),
        )
        lane_id = cur.lastrowid
    queue_cadence(db_path, lane_id)  # no sent_date arg — reads from lane
    touches = due_cadence_touches(db_path, "2026-08-28")
    assert len(touches) == 1


# --- new: mark_lane_sent ---

def test_mark_lane_sent(db_path):
    opp_id = _opp(db_path)
    lane_id = _lane(db_path, opp_id)
    mark_lane_sent(db_path, lane_id)
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT status, sent_at FROM outreach_lanes WHERE id = ?", (lane_id,)
        ).fetchone()
    assert row["status"] == "sent"
    assert row["sent_at"] is not None


# --- new: network_activation_due ---

def test_network_activation_due_returns_untouched(db_path):
    # Contact must be tied to an active Tier A/B company (grill Q2 decision).
    _opp(db_path, company="acme")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, email, company, degree, source, added_at) "
            "VALUES ('Alice', 'a@x.com', 'acme', 1, 'linkedin_csv', ?)",
            (now,),
        )
    contacts = network_activation_due(db_path, "2026-08-25", limit=5)
    assert len(contacts) >= 1


def test_network_activation_ranks_decision_makers_first(db_path):
    # Both contacts tied to active Tier A company so the ranking can be tested.
    _opp(db_path, company="rankco")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, title, company, degree, source, added_at) "
            "VALUES ('Bob', 'VP Sales', 'rankco', 1, 'linkedin_csv', ?)",
            (now,),
        )
        cur.execute(
            "INSERT INTO contacts (name, title, company, degree, source, added_at) "
            "VALUES ('Carol', 'SDR', 'rankco', 1, 'linkedin_csv', ?)",
            (now,),
        )
    contacts = network_activation_due(db_path, "2026-08-25", limit=5)
    names = [c["name"] for c in contacts]
    assert names.index("Bob") < names.index("Carol")


# --- new: pending_secondary_escalations ---

def test_pending_secondary_escalations_empty(db_path):
    assert pending_secondary_escalations(db_path) == []
