"""Tests for Network Activation Lite (Tier A/B filter) and No-Open-Role Lite."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from banks.governance import network_activation_due, no_open_role_candidates
from banks.opportunity import record_opportunity
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _opp(db_path, company="acme", tier="A"):
    return record_opportunity(
        db_path, "VP Sales", "simplify", 80,
        tier=tier, company_normalized=company, industry="SaaS",
    )


def _contact(db_path, name, company, degree=1, title="", email="", verified=0):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, degree, title, email, verified, source, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'linkedin_csv', ?)",
            (name, company, degree, title, email, verified, now),
        )
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Network Activation Lite
# ---------------------------------------------------------------------------

class TestNetworkActivationLite:
    def test_only_tier_ab_contacts_returned(self, db_path):
        """Contact at a company with no active opportunity must not appear."""
        _contact(db_path, "Ghost", "nowhere_corp", degree=1)
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        assert all(c["company"] != "nowhere_corp" for c in contacts)

    def test_tier_a_contact_appears(self, db_path):
        _opp(db_path, company="targetco", tier="A")
        _contact(db_path, "Alice", "targetco", degree=1)
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        assert any(c["name"] == "Alice" for c in contacts)

    def test_tier_b_contact_appears(self, db_path):
        _opp(db_path, company="bco", tier="B")
        _contact(db_path, "Bob", "bco", degree=1)
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        assert any(c["name"] == "Bob" for c in contacts)

    def test_degree1_ranks_before_degree2(self, db_path):
        _opp(db_path, company="mixco")
        _contact(db_path, "Senior", "mixco", degree=1, title="SDR")
        _contact(db_path, "Junior", "mixco", degree=2, title="VP Sales")
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        names = [c["name"] for c in contacts]
        assert names.index("Senior") < names.index("Junior")

    def test_cap_respected(self, db_path):
        _opp(db_path, company="bigco")
        for i in range(10):
            _contact(db_path, f"Person{i}", "bigco", degree=1)
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        assert len(contacts) <= 5

    def test_recently_touched_excluded(self, db_path):
        _opp(db_path, company="touchco")
        cid = _contact(db_path, "Touched", "touchco", degree=1, email="t@x.com")
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        # Simulate a recent outreach lane sent today
        with cursor(db_path) as cur:
            cur.execute(
                "INSERT INTO outreach_lanes (opportunity_id, lane_type, contact_id, sent_at, created_at) "
                "VALUES (1, 'hiring_manager', ?, ?, ?)",
                (cid, now, now),
            )
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        assert all(c["name"] != "Touched" for c in contacts)

    def test_suggested_channel_email_if_verified(self, db_path):
        """Not a governance test — but confirm verified email field is returned for caller."""
        _opp(db_path, company="emailco")
        _contact(db_path, "Vera", "emailco", degree=1, email="v@x.com", verified=1)
        contacts = network_activation_due(db_path, "2026-08-28", limit=5)
        vera = next((c for c in contacts if c["name"] == "Vera"), None)
        assert vera is not None
        assert vera["verified"] == 1
        assert vera["email"] == "v@x.com"


# ---------------------------------------------------------------------------
# No-Open-Role Lite
# ---------------------------------------------------------------------------

class TestNoOpenRoleLite:
    def test_company_with_no_opp_and_warm_contact(self, db_path):
        _contact(db_path, "Alice", "pitchme_corp", degree=1)
        candidates = no_open_role_candidates(db_path, "2026-08-28", limit=3)
        assert any(c["company"] == "pitchme_corp" for c in candidates)

    def test_company_with_active_opp_excluded(self, db_path):
        _opp(db_path, company="active_corp")
        _contact(db_path, "Bob", "active_corp", degree=1)
        candidates = no_open_role_candidates(db_path, "2026-08-28", limit=3)
        assert all(c["company"] != "active_corp" for c in candidates)

    def test_degree2_contact_not_surfaced(self, db_path):
        """Only degree=1 warm contacts trigger no-open-role."""
        _contact(db_path, "Carol", "cold_corp", degree=2)
        candidates = no_open_role_candidates(db_path, "2026-08-28", limit=3)
        assert all(c["company"] != "cold_corp" for c in candidates)

    def test_14day_cooldown_suppresses_resurface(self, db_path):
        _contact(db_path, "Dave", "cooldown_corp", degree=1)
        # Log a recent no-open-role touch
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with cursor(db_path) as cur:
            cur.execute(
                "INSERT INTO touch_log (address, draft_ref, touched_at) VALUES (?, 'ref:1', ?)",
                ("no_open_role:cooldown_corp", now),
            )
        candidates = no_open_role_candidates(db_path, "2026-08-28", limit=3)
        assert all(c["company"] != "cooldown_corp" for c in candidates)

    def test_cap_respected(self, db_path):
        for i in range(6):
            _contact(db_path, f"Person{i}", f"corp{i}", degree=1)
        candidates = no_open_role_candidates(db_path, "2026-08-28", limit=3)
        assert len(candidates) <= 3

    def test_contact_included_in_result(self, db_path):
        _contact(db_path, "Eve", "eveco", degree=1, email="e@x.com")
        candidates = no_open_role_candidates(db_path, "2026-08-28", limit=3)
        eveco = next((c for c in candidates if c["company"] == "eveco"), None)
        assert eveco is not None
        assert eveco["contact"]["name"] == "Eve"


# ---------------------------------------------------------------------------
# Client policy: no proactive consulting unless flag enabled
# ---------------------------------------------------------------------------

class TestProactiveConsultingGate:
    def _facts(self):
        from banks.opportunity import CareerFacts
        return CareerFacts(identity="GTM leader", experience=("VP Sales",),
                           skills=("enterprise sales",), seeking="VP Sales / CRO")

    def test_cards_suppressed_when_flag_off(self, db_path, monkeypatch):
        from banks.attack_queue import _no_open_role_cards
        from banks.chatport import FakeChatPort
        from banks.config import BanksConfig
        _contact(db_path, "Alice", "pitchme_corp", degree=1, email="a@x.com")
        monkeypatch.setattr("banks.config.load_config",
                            lambda: BanksConfig(None, None, proactive_consulting_enabled=False))
        cards = _no_open_role_cards(db_path, "2026-08-28", self._facts(), FakeChatPort())
        assert cards == []

    def test_cards_surface_when_flag_on(self, db_path, monkeypatch):
        from banks.attack_queue import _no_open_role_cards
        from banks.chatport import FakeChatPort
        from banks.config import BanksConfig
        _contact(db_path, "Alice", "pitchme_corp", degree=1, email="a@x.com")
        monkeypatch.setattr("banks.config.load_config",
                            lambda: BanksConfig(None, None, proactive_consulting_enabled=True))
        cards = _no_open_role_cards(db_path, "2026-08-28", self._facts(), FakeChatPort())
        assert any(c.kind == "no_open_role" for c in cards)
