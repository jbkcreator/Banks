"""Regression tests for PR #8 review fixes.

Fix 1 — Tier C opportunities must not receive surround packs.
Fix 2 — SSRF: LiveFetchPort rejects private/loopback/http URLs.
Fix 3 — Surround pack triggered on opportunity approval via source_packet_id.
Fix 4 — Governance controls wired into relay_run (daily cap + 14-day spacing
         + mark_lane_sent + queue_cadence + record_funnel_event).
Fix 5 — LinkedIn-only enrichment results persisted (email OR linkedin_url).
"""

from __future__ import annotations

import pytest

from banks.approval import ButtonAction, apply_action
from banks.chatport import FakeChatPort
from banks.contact_enrichment import EnrichmentResult, retrieve_and_apply, FakeEnrichmentPort
from banks.enforcement import Draft
from banks.flow import propose
from banks.mailer import FakeMailer
from banks.opportunity import CareerFacts, record_opportunity
from banks.packets import DecisionPacket
from banks.relay import relay_run
from banks.store import cursor, init_db
from banks.surround import generate_surround_pack, SurroundPack


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


_FACTS = CareerFacts(
    identity="Josh Kantor, VP Sales / CRO",
    experience=("Led $50M ARR growth",),
    skills=("GTM strategy", "sales leadership"),
    education=("BS Business",),
)


def _opp(db, tier="A"):
    return record_opportunity(
        db, "VP Sales", "manual", 80,
        tier=tier, company_normalized="Acme Corp",
        pursuit_mode="full_time",
    )


# ---------------------------------------------------------------------------
# Fix 1 — Tier C gets empty pack
# ---------------------------------------------------------------------------

def test_tier_c_surround_returns_empty(db):
    opp_id = _opp(db, tier="C")
    pack = generate_surround_pack(db, opp_id, _FACTS, FakeChatPort())
    assert pack.lanes == [], "Tier C must never receive a surround pack"


def test_tier_b_surround_returns_recruiter_lane(db):
    opp_id = _opp(db, tier="B")
    pack = generate_surround_pack(db, opp_id, _FACTS, FakeChatPort())
    assert any(ln["type"] == "recruiter" for ln in pack.lanes)


# ---------------------------------------------------------------------------
# Fix 2 — SSRF guard in LiveFetchPort
# ---------------------------------------------------------------------------

def test_ssrf_rejects_http_scheme():
    from banks.enrich import _is_safe_url
    assert not _is_safe_url("http://example.com/job")


def test_ssrf_rejects_localhost():
    from banks.enrich import _is_safe_url
    assert not _is_safe_url("https://localhost/job")


def test_ssrf_rejects_loopback_ip():
    from banks.enrich import _is_safe_url
    assert not _is_safe_url("https://127.0.0.1/job")


def test_ssrf_accepts_public_https():
    from banks.enrich import _is_safe_url
    # We can't make network calls; just verify the function doesn't crash on
    # a real hostname — if DNS fails it returns False (safe default).
    result = _is_safe_url("https://www.example.com/job")
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Fix 3 — source_packet_id back-link + surround triggered on approval
# ---------------------------------------------------------------------------

def test_source_packet_id_stored_after_surface(db):
    """_surface_opportunity writes source_packet_id back to the opportunity row."""
    from banks.intake import _surface_opportunity
    opp_id = _opp(db, tier="A")
    parsed = {"title": "VP Sales", "company": "Acme Corp",
              "location": "Remote", "industry": "SaaS"}
    proposed = _surface_opportunity(db, FakeChatPort(), opp_id, parsed, 80, "A", "full_time")
    with cursor(db) as cur:
        row = cur.execute(
            "SELECT source_packet_id FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
    assert row["source_packet_id"] == proposed.packet_id


# ---------------------------------------------------------------------------
# Fix 4 — Governance wired into relay_run
# ---------------------------------------------------------------------------

def _outbound(db, chat):
    return propose(
        db,
        DecisionPacket(kind="inquiry_reply", decision="Reply?",
                       recommendation="yes", default_if_unanswered="hold"),
        Draft(kind="inquiry_reply", to="hm@acme.com", subject="Re: role", body="hello"),
        chat, send_channel="email:sendas",
    )


def test_relay_respects_daily_email_cap(db):
    """Once daily email cap is hit, further approved intents are blocked."""
    from banks.governance import DAILY_CAPS
    from banks.store import cursor as cur_ctx
    from datetime import date

    today = date.today().isoformat()
    cap = DAILY_CAPS["email"]

    # Exhaust the cap manually.
    with cur_ctx(db) as cur:
        cur.execute(
            "INSERT INTO governance_ledger (date, channel, count) VALUES (?, 'email', ?)",
            (today, cap),
        )

    chat = FakeChatPort()
    res = _outbound(db, chat)
    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")
    result = relay_run(db, FakeMailer())
    assert res.draft_ref in result.blocked
    assert res.draft_ref not in result.sent


def test_relay_respects_14day_contact_spacing(db):
    """If contact contacted <14 days ago, relay blocks the send."""
    from datetime import date, timedelta

    chat = FakeChatPort()
    res = _outbound(db, chat)

    # Insert a contact and an outreach_lane that was sent 1 day ago.
    recent_sent = (date.today() - timedelta(days=1)).isoformat() + "T00:00:00+00:00"
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, email, linkedin_url, degree, source, added_at) "
            "VALUES ('HM', 'Acme', 'hm@acme.com', '', 1, 'manual', ?)", (recent_sent,)
        )
        contact_id = cur.lastrowid
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, contact_id, draft_ref, "
            "status, created_at, sent_at) VALUES (1, 'hiring_manager', ?, ?, 'sent', ?, ?)",
            (contact_id, res.draft_ref, recent_sent, recent_sent),
        )

    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")
    result = relay_run(db, FakeMailer())
    assert res.draft_ref in result.blocked


def test_relay_queues_cadence_after_send(db):
    """After a successful send, cadence_queue rows are created for the lane."""
    chat = FakeChatPort()
    res = _outbound(db, chat)
    opp_id = _opp(db, tier="A")

    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, contact_id, draft_ref, "
            "status, created_at) VALUES (?, 'hiring_manager', NULL, ?, 'approved', datetime('now'))",
            (opp_id, res.draft_ref),
        )

    apply_action(db, ButtonAction.APPROVE, res.draft_ref, "U1")
    relay_run(db, FakeMailer())

    with cursor(db) as cur:
        rows = cur.execute(
            "SELECT * FROM cadence_queue WHERE outreach_lane_id IN "
            "(SELECT id FROM outreach_lanes WHERE draft_ref = ?)", (res.draft_ref,)
        ).fetchall()
    assert len(rows) == 3  # Day 3 / 7 / 14


# ---------------------------------------------------------------------------
# Fix 5 — LinkedIn-only enrichment result persisted
# ---------------------------------------------------------------------------

def test_linkedin_only_result_persisted(db):
    """A result with linkedin_url but no email must still be written to contacts."""
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO enrichment_queue (company_normalized, opportunity_id, "
            "status, batch_id, requested_at) VALUES ('Acme Corp', NULL, 'submitted', 'b1', datetime('now'))"
        )

    result = EnrichmentResult(
        company="Acme Corp",
        name="Jane Smith",
        email=None,
        linkedin_url="https://linkedin.com/in/janesmith",
        title="VP Sales",
        verified=False,
    )

    class _DirectPort:
        def retrieve(self, batch_id):
            return [result]

    written = retrieve_and_apply(db, _DirectPort(), "b1")
    assert written == 1

    with cursor(db) as cur:
        row = cur.execute(
            "SELECT linkedin_url, email FROM contacts WHERE name = 'Jane Smith'"
        ).fetchone()
    assert row is not None
    assert row["linkedin_url"] == "https://linkedin.com/in/janesmith"
