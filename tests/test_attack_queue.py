"""MOD-05 Daily Attack Queue — build_sections + post idempotency."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.attack_queue import build_sections, post_daily_queue
from banks.chatport import FakeChatPort
from banks.governance import freeze_company, record_funnel_event
from banks.opportunity import CareerFacts, record_opportunity
from banks.store import cursor, init_db

FACTS = CareerFacts(
    identity="GTM leader",
    experience=("VP Sales at PropTech Co",),
    skills=("enterprise sales",),
    seeking="VP Sales roles",
)


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _pending_lane(db_path, opp_id, lane_type="hiring_manager", subject="Hi", score=None):
    """Create a pending outreach_lane + its pending send_intent (a live card)."""
    now = _now().isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, status, created_at, draft_ref) "
            "VALUES (?, ?, 'pending', ?, NULL)",
            (opp_id, lane_type, now),
        )
        lane_id = cur.lastrowid
        # draft_ref is a decision-packet id in production; a synthetic id is fine here
        ref = str(1000 + lane_id)
        cur.execute("UPDATE outreach_lanes SET draft_ref = ? WHERE id = ?", (ref, lane_id))
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, status, created_at) "
            "VALUES (?, 'none:internal', 'x@y.com', ?, 'body', 'pending', ?)",
            (ref, subject, now),
        )
    return ref


def test_empty_db_honest_empty(db_path):
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    assert len(sections) == 1
    assert "Nothing queued" in sections[0].lines[0]


def test_tier_a_cards_appear(db_path):
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme", industry="PropTech")
    _pending_lane(db_path, opp, "hiring_manager", subject="Reach HM")
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    titles = [s.title for s in sections]
    assert any("Tier A" in t for t in titles)
    tier_a = next(s for s in sections if "Tier A" in s.title)
    assert len(tier_a.cards) == 1
    assert tier_a.cards[0]["draft_ref"]


def test_career_facts_empty_blocker_line_not_crash(db_path):
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme", industry="PropTech")
    _pending_lane(db_path, opp, "hiring_manager")
    sections = build_sections(db_path, now=_now(), career_facts=CareerFacts())
    tier_a = next(s for s in sections if "Tier A" in s.title)
    assert tier_a.cards == []
    assert "career-facts.md is empty" in tier_a.lines[0]


def test_empty_sections_omitted(db_path):
    # Only an imported digest should show — no active convo / follow-up sections
    record_opportunity(db_path, "Role", "simplify", 40, tier="C",
                       company_normalized="acme")
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    titles = [s.title for s in sections]
    assert not any("Active conversations" in t for t in titles)
    assert not any("Follow-ups" in t for t in titles)
    assert any("Imported" in t for t in titles)


def test_failure_mode_first_order(db_path):
    # active conversation + a tier A card → carried/active come before Tier A
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme", industry="PropTech")
    _pending_lane(db_path, opp, "hiring_manager")
    freeze_company(db_path, "acme", reason="got_reply")
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    titles = [s.title for s in sections]
    assert titles.index(next(t for t in titles if "Active" in t)) < \
           titles.index(next(t for t in titles if "Tier A" in t))


def test_imported_digest_counts(db_path):
    record_opportunity(db_path, "A", "simplify", 90, tier="A", company_normalized="a")
    record_opportunity(db_path, "B", "simplify", 60, tier="B", company_normalized="b")
    record_opportunity(db_path, "C", "simplify", 40, tier="C", company_normalized="c",
                       needs_enrichment=True)
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    digest = next(s for s in sections if "Imported" in s.title)
    line = digest.lines[0]
    assert "3 tracked" in line
    assert "held for enrichment" in line


def test_funnel_footer(db_path):
    opp = record_opportunity(db_path, "VP", "simplify", 90, tier="A", company_normalized="a")
    record_funnel_event(db_path, opp, "applied")
    record_funnel_event(db_path, opp, "contacted")
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    funnel = next(s for s in sections if "Funnel" in s.title)
    assert "applied" in funnel.lines[0] and "contacted" in funnel.lines[0]


def test_score_ranking_within_tier(db_path):
    hi = record_opportunity(db_path, "High", "simplify", 95, tier="A", company_normalized="hi")
    lo = record_opportunity(db_path, "Low", "simplify", 76, tier="A", company_normalized="lo")
    _pending_lane(db_path, lo, "hiring_manager", subject="LowCo")
    _pending_lane(db_path, hi, "hiring_manager", subject="HighCo")
    sections = build_sections(db_path, now=_now(), career_facts=FACTS)
    tier_a = next(s for s in sections if "Tier A" in s.title)
    # higher score first
    assert tier_a.cards[0]["subject"] == "HighCo"


# --- post_daily_queue -------------------------------------------------------

def test_post_is_idempotent(db_path):
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme")
    _pending_lane(db_path, opp, "hiring_manager")
    chat = FakeChatPort()
    now = _now()
    r1 = post_daily_queue(db_path, chat, now=now, career_facts=FACTS)
    posts_after_first = len(chat.posts)
    r2 = post_daily_queue(db_path, chat, now=now, career_facts=FACTS)
    assert r1["skipped"] is False
    assert r2["skipped"] is True
    assert len(chat.posts) == posts_after_first  # no duplicate posting


def test_post_threads_cards_under_root(db_path):
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme")
    _pending_lane(db_path, opp, "hiring_manager")
    chat = FakeChatPort()
    res = post_daily_queue(db_path, chat, now=_now(), career_facts=FACTS)
    root_ts = res["root_ts"]
    # summary post has no thread_ts; the card is threaded under the root
    summary = chat.posts[0]
    assert summary["thread_ts"] is None
    card_posts = [p for p in chat.posts if p["thread_ts"] == root_ts]
    assert len(card_posts) == res["cards"] >= 1


def test_post_records_queue_items(db_path):
    opp = record_opportunity(db_path, "VP Sales", "simplify", 90, tier="A",
                             company_normalized="acme")
    ref = _pending_lane(db_path, opp, "hiring_manager")
    post_daily_queue(db_path, FakeChatPort(), now=_now(), career_facts=FACTS)
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT state, card_ts FROM queue_items WHERE draft_ref = ?", (ref,)
        ).fetchone()
    assert row["state"] == "active"
    assert row["card_ts"]
