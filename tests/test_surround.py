"""MOD-03 Surround Pack tests."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.opportunity import CareerFacts, record_opportunity
from banks.store import cursor, init_db
from banks.surround import (
    advance_warm_intro,
    generate_surround_pack,
    stall_aged_warm_intros,
)

FACTS = CareerFacts(
    identity="GTM leader with 15 years building sales orgs",
    experience=("VP Sales at PropTech Co", "Director GTM at SaaS Co"),
    skills=("enterprise sales", "GTM strategy", "team building"),
    seeking="VP Sales or CRO roles in PropTech/SaaS",
)


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _insert_contact(db_path, company, verified=True, email="jane@acme.com"):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts "
            "(name, company, email, linkedin_url, degree, source, verified, added_at) "
            "VALUES ('Jane Smith', ?, ?, 'https://linkedin.com/in/jane', "
            "1, 'linkedin_csv', ?, ?)",
            (company, email, 1 if verified else 0, now),
        )
        return cur.lastrowid


def _make_opp(db_path, tier="A", company="Acme", contact_id=None):
    return record_opportunity(
        db_path,
        f"{tier} Role at {company}",
        "simplify",
        80,
        tier=tier,
        company_normalized=company.lower(),
        industry="PropTech",
        contact_id=contact_id,
    )


def test_tier_a_generates_pov_brief(db_path):
    opp_id = _make_opp(db_path, tier="A")
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    types = [l["type"] for l in pack.lanes]
    assert "pov_brief" in types
    assert "recruiter" in types


def test_tier_b_recruiter_only(db_path):
    """Tier B → recruiter lane only, no hiring manager, warm intro, or POV brief."""
    opp_id = _make_opp(db_path, tier="B")
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    types = [l["type"] for l in pack.lanes]
    assert "recruiter" in types
    assert "pov_brief" not in types
    assert "hiring_manager" not in types
    assert "warm_intro" not in types


def test_verified_contact_uses_hiring_manager_lane(db_path):
    cid = _insert_contact(db_path, "Acme", verified=True)
    opp_id = _make_opp(db_path, tier="A", contact_id=cid)
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    types = [l["type"] for l in pack.lanes]
    assert "hiring_manager" in types
    assert "linkedin" not in types


def test_contact_with_no_email_uses_linkedin_lane(db_path):
    """No usable address -> LinkedIn DM. This is the fallback, not a punishment."""
    cid = _insert_contact(db_path, "Acme", verified=False, email="")
    opp_id = _make_opp(db_path, tier="A", contact_id=cid)
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    types = [l["type"] for l in pack.lanes]
    assert "linkedin" in types
    assert "hiring_manager" not in types


def test_unverified_contact_with_an_email_still_gets_the_email_lane(db_path):
    """Policy change 2026-09-02: routing gates on having an address, not on the
    `verified` flag. Only provider enrichment ever set verified=1, so requiring
    it sent all 1,694 of Josh's contacts down the LinkedIn lane — including the
    17 who had a perfectly good address."""
    cid = _insert_contact(db_path, "Acme", verified=False, email="jane@acme.com")
    opp_id = _make_opp(db_path, tier="A", contact_id=cid)
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    assert "hiring_manager" in [l["type"] for l in pack.lanes]


def test_empty_facts_raises(db_path):
    opp_id = _make_opp(db_path, tier="A")
    with pytest.raises(ValueError, match="career-facts"):
        generate_surround_pack(db_path, opp_id, CareerFacts(), FakeChatPort())


def test_multiple_cards_posted(db_path):
    cid = _insert_contact(db_path, "Acme")
    opp_id = _make_opp(db_path, tier="A", contact_id=cid)
    chat = FakeChatPort()
    generate_surround_pack(db_path, opp_id, FACTS, chat)
    # hiring_manager + warm_intro + recruiter + pov_brief = 4
    assert len(chat.posts) >= 3


def test_frozen_company_returns_empty_pack(db_path):
    from banks.governance import freeze_company
    opp_id = _make_opp(db_path, tier="A", company="Frozen Corp")
    freeze_company(db_path, "frozen corp", reason="got_reply")
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    assert pack.lanes == []


def test_lanes_recorded_in_db(db_path):
    opp_id = _make_opp(db_path, tier="A")
    generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    with cursor(db_path) as cur:
        n = cur.execute(
            "SELECT COUNT(*) n FROM outreach_lanes WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()["n"]
    assert n >= 1


def test_warm_intro_created_in_db(db_path):
    cid = _insert_contact(db_path, "Acme")
    opp_id = _make_opp(db_path, tier="A", contact_id=cid)
    generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT state FROM warm_intros WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()
    assert row is not None
    assert row["state"] == "ASKED"


def test_advance_warm_intro(db_path):
    cid = _insert_contact(db_path, "Acme")
    opp_id = _make_opp(db_path, tier="A", contact_id=cid)
    generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    advance_warm_intro(db_path, opp_id, cid, "AGREED")
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT state FROM warm_intros WHERE opportunity_id = ? AND contact_id = ?",
            (opp_id, cid),
        ).fetchone()
    assert row["state"] == "AGREED"


def test_advance_invalid_state_raises(db_path):
    with pytest.raises(ValueError):
        advance_warm_intro(db_path, 1, 1, "INVALID")


def test_stall_aged_warm_intros(db_path):
    opp_id = _make_opp(db_path, tier="A")
    cid = _insert_contact(db_path, "Acme")
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO warm_intros "
            "(opportunity_id, contact_id, state, asked_at, state_changed_at) "
            "VALUES (?, ?, 'ASKED', ?, ?)",
            (opp_id, cid, old, old),
        )
    count = stall_aged_warm_intros(db_path, stall_after_days=7)
    assert count >= 1
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT state FROM warm_intros WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()
    assert row["state"] == "STALLED"


def test_stall_does_not_affect_recent(db_path):
    opp_id = _make_opp(db_path, tier="A")
    cid = _insert_contact(db_path, "Acme")
    recent = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO warm_intros "
            "(opportunity_id, contact_id, state, asked_at, state_changed_at) "
            "VALUES (?, ?, 'ASKED', ?, ?)",
            (opp_id, cid, recent, recent),
        )
    count = stall_aged_warm_intros(db_path, stall_after_days=7)
    assert count == 0


def test_advance_manual_stalled_raises(db_path):
    """STALLED is auto-only — manual attempt must raise."""
    with pytest.raises(ValueError, match="STALLED"):
        advance_warm_intro(db_path, 1, 1, "STALLED")


def test_stall_creates_secondary_escalation_lane(db_path):
    opp_id = _make_opp(db_path, tier="A")
    cid = _insert_contact(db_path, "Acme")
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO warm_intros "
            "(opportunity_id, contact_id, state, asked_at, state_changed_at) "
            "VALUES (?, ?, 'ASKED', ?, ?)",
            (opp_id, cid, old, old),
        )
    stall_aged_warm_intros(db_path, stall_after_days=7)
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT lane_type, status FROM outreach_lanes "
            "WHERE opportunity_id = ? AND lane_type = 'secondary_escalation'",
            (opp_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "pending"


def test_consulting_lane_added_for_fractional_mode(db_path):
    opp_id = record_opportunity(
        db_path, "Fractional CRO", "simplify", 80,
        tier="A", company_normalized="acme", industry="SaaS",
        pursuit_mode="fractional",
    )
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    types = [l["type"] for l in pack.lanes]
    assert "consulting" in types


def test_consulting_lane_added_tier_b_fractional(db_path):
    opp_id = record_opportunity(
        db_path, "Fractional Head of Sales", "simplify", 60,
        tier="B", company_normalized="acme", industry="SaaS",
        pursuit_mode="consulting",
    )
    pack = generate_surround_pack(db_path, opp_id, FACTS, FakeChatPort())
    types = [l["type"] for l in pack.lanes]
    assert "consulting" in types
    assert "recruiter" in types
