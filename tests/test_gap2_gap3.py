"""Tests for Gap 2 (LinkedIn deep-link handoff) and Gap 3 (email intake)."""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.emailport import FakeEmailPort, LiveImapEmailPort, is_confirmation_email
from banks.lanes import _linkedin_action_line, draft_linkedin_lane, draft_employee_lane
from banks.opportunity import CareerFacts
from banks.intake import ingest_email_confirmations
from banks.store import init_db
from banks.chatport import FakeChatPort


_FACTS = CareerFacts(
    identity="GTM leader",
    experience=("VP Sales at PropTech",),
    skills=("enterprise sales",),
    seeking="VP Sales / CRO",
)


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


# ---------------------------------------------------------------------------
# Gap 2 — LinkedIn deep-link handoff
# ---------------------------------------------------------------------------

class TestLinkedInHandoff:
    def test_dm_compose_link_built_from_slug(self):
        contact = {"name": "Alice", "linkedin_url": "https://linkedin.com/in/alice-smith"}
        line = _linkedin_action_line(contact)
        assert "messaging/thread/new/?recipient=alice-smith" in line

    def test_trailing_slash_stripped(self):
        contact = {"linkedin_url": "https://www.linkedin.com/in/bob-jones/"}
        line = _linkedin_action_line(contact)
        assert "recipient=bob-jones" in line
        assert "bob-jones/" not in line

    def test_missing_url_returns_flag(self):
        line = _linkedin_action_line({"name": "Carol", "linkedin_url": ""})
        assert "No LinkedIn URL on file" in line

    def test_none_url_returns_flag(self):
        line = _linkedin_action_line({"name": "Dave"})
        assert "No LinkedIn URL on file" in line

    def test_non_standard_url_links_profile(self):
        contact = {"linkedin_url": "https://linkedin.com/pub/eve/123"}
        line = _linkedin_action_line(contact)
        assert "linkedin.com/pub/eve/123" in line

    def test_linkedin_lane_includes_dm_link(self):
        contact = {"name": "Frank", "linkedin_url": "https://linkedin.com/in/frank-jones"}
        draft = draft_linkedin_lane("VP Sales", "Acme", contact, _FACTS)
        assert "messaging/thread/new/?recipient=frank-jones" in draft.body

    def test_linkedin_lane_flags_missing_url(self):
        contact = {"name": "Grace", "linkedin_url": ""}
        draft = draft_linkedin_lane("VP Sales", "Acme", contact, _FACTS)
        assert "No LinkedIn URL on file" in draft.body

    def test_employee_lane_adds_link_when_no_email(self):
        contact = {"name": "Hank", "linkedin_url": "https://linkedin.com/in/hank", "verified": 0}
        draft = draft_employee_lane("VP Sales", "Acme", contact, _FACTS)
        assert "messaging/thread/new/?recipient=hank" in draft.body

    def test_employee_lane_no_link_when_verified_email(self):
        contact = {"name": "Ivy", "email": "i@x.com", "verified": 1,
                   "linkedin_url": "https://linkedin.com/in/ivy"}
        draft = draft_employee_lane("VP Sales", "Acme", contact, _FACTS)
        # Has verified email — LinkedIn link not needed
        assert "messaging/thread/new" not in draft.body


# ---------------------------------------------------------------------------
# Gap 3 — forwarded email confirmation listener
# ---------------------------------------------------------------------------

class TestEmailIntake:
    def test_confirmation_email_detected(self):
        assert is_confirmation_email("Your application to Appfolio has been received")
        assert is_confirmation_email("Thank you for applying to Buildium")
        assert not is_confirmation_email("Weekly newsletter from LinkedIn")

    def test_fake_port_returns_messages(self):
        port = FakeEmailPort([{"subject": "Application received — Acme", "body": ""}])
        msgs = port.get_confirmations()
        assert len(msgs) == 1

    def test_ingest_email_confirmations_records_opp(self, db_path):
        port = FakeEmailPort([
            {"subject": "Your application to AppFolio was received", "body": "", "from": "", "date": ""},
        ])
        ingested, skipped = ingest_email_confirmations(db_path, port, FakeChatPort())
        assert ingested == 1
        assert skipped == 0

    def test_non_confirmation_skipped(self, db_path):
        port = FakeEmailPort([
            {"subject": "LinkedIn weekly digest", "body": "Updates from your network", "from": "", "date": ""},
        ])
        ingested, skipped = ingest_email_confirmations(db_path, port, FakeChatPort())
        assert ingested == 0
        assert skipped == 1

    def test_duplicate_skipped(self, db_path):
        port = FakeEmailPort([
            {"subject": "Application received — DupeCo", "body": "", "from": "", "date": ""},
            {"subject": "Application received — DupeCo", "body": "", "from": "", "date": ""},
        ])
        ingested, skipped = ingest_email_confirmations(db_path, port, FakeChatPort())
        assert ingested == 1
        assert skipped == 1

    def test_excluded_company_skipped(self, db_path):
        from banks.store import cursor
        from banks.store import init_db as _init
        import datetime
        with cursor(db_path) as cur:
            # Store the normalised slug ("Excluded Corp" → "excluded"); is_company_excluded
            # normalises the subject-derived name the same way before matching.
            cur.execute("INSERT INTO company_exclusions (company_normalized, added_at) VALUES (?,?)",
                        ("excluded", datetime.datetime.utcnow().isoformat()))
        port = FakeEmailPort([
            {"subject": "Your application to Excluded Corp was received", "body": "", "from": "", "date": ""},
        ])
        ingested, skipped = ingest_email_confirmations(db_path, port, FakeChatPort())
        assert ingested == 0
        assert skipped == 1

    def test_live_imap_port_exists(self):
        # Just verify the class is importable and instantiable with credentials
        port = LiveImapEmailPort("test@gmail.com", "app-password")
        assert port._email == "test@gmail.com"

    def test_live_imap_port_handles_network_failure_gracefully(self):
        # Bad credentials → IMAP auth error → returns [] without crashing
        port = LiveImapEmailPort("bad@gmail.com", "wrong-password")
        result = port.get_confirmations()
        assert result == []

    def test_intake_job_noops_without_creds(self, db_path):
        # run_job('email_intake_poll') must no-op (not raise) when unprovisioned.
        from banks import jobs
        from banks.config import BanksConfig
        import banks.config as cfgmod
        orig = cfgmod.load_config
        cfgmod.load_config = lambda: BanksConfig(None, None)
        try:
            assert jobs.run_job("email_intake_poll", db_path, FakeChatPort()) is None
        finally:
            cfgmod.load_config = orig

    def test_intake_job_ingests_via_live_port(self, db_path, monkeypatch):
        # With creds set, the job builds the IMAP port and records confirmations.
        from banks import jobs
        from banks.config import BanksConfig
        monkeypatch.setattr(
            "banks.config.load_config",
            lambda: BanksConfig(None, None, intake_email="jbkantor@gmail.com",
                                intake_email_password="app-pw"))
        monkeypatch.setattr(
            "banks.emailport.LiveImapEmailPort",
            lambda email, pw: FakeEmailPort([
                {"subject": "Your application to AppFolio was received",
                 "body": "", "from": "", "date": ""}]))
        result = jobs.run_job("email_intake_poll", db_path, FakeChatPort())
        assert result == {"ingested": 1, "skipped": 0}
