"""Tests for Gap 2 (LinkedIn deep-link handoff) and Gap 3 (email intake)."""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.emailport import (FakeEmailPort, LiveImapEmailPort, extract_company,
                             extract_company_from_subject, is_confirmation_email)
from banks.lanes import _linkedin_action_line, draft_linkedin_lane, draft_employee_lane
from banks.opportunity import CareerFacts
from banks.intake import ingest_email_confirmations
from banks.store import cursor, init_db
from banks.chatport import FakeChatPort
from banks.llmport import FakeLLMPort
from banks.opportunity import record_opportunity


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

class TestCompanyExtraction:
    """extract_company: subject -> forwarded body headers -> body text -> sender
    domain, so a real forwarded confirmation resolves even when the subject
    names no company."""

    def test_subject_wins_when_present(self):
        assert extract_company("Thank you for applying to Acme") == "Acme"

    def test_subjectless_falls_to_forwarded_subject_header(self):
        # Josh forwards: envelope subject is generic, real subject is in the body.
        body = ("---------- Forwarded message ---------\n"
                "From: Careers <careers@acme.com>\n"
                "Subject: Your application to Northspyre was received\n\n"
                "Thanks for applying.")
        assert extract_company("Fwd: (no subject)", body) == "Northspyre"

    def test_forwarded_from_domain_used_when_no_phrasing(self):
        body = ("---------- Forwarded message ---------\n"
                "From: EliseAI Talent <no-reply@eliseai.com>\n"
                "Subject: We received your application\n\n"
                "Your application is in review.")
        assert extract_company("Fwd: application", body) == "Eliseai"

    def test_ats_domain_is_not_treated_as_company(self):
        # greenhouse/lever/etc name the ATS, not the employer -> skip, fall through.
        body = ("From: no-reply@greenhouse.io\n"
                "Subject: Application received\n")
        assert extract_company("Fwd: application received", body) == ""

    def test_sender_domain_when_direct_not_forwarded(self):
        assert extract_company("Application received", "",
                               "jobs@northspyre.com") == "Northspyre"

    def test_gmail_sender_is_generic(self):
        assert extract_company("Application received", "",
                               "jbkantor@gmail.com") == ""

    def test_subject_extractor_unchanged(self):
        assert extract_company_from_subject("applied to Bay Street") == "Bay Street"


class TestEmailIntake:
    """Match-and-confirm: email never CREATES an opportunity — it only confirms
    one Josh already applied to (from the Simplify CSV), gated by the LLM. This
    kills the personal-inbox junk that the old create-from-email path produced."""

    def _seed(self, db_path, company="acme"):
        return record_opportunity(db_path, "AE role", "simplify", 60, tier="B",
                                  company_normalized=company, status="sourced")

    def test_confirmation_email_detected(self):
        assert is_confirmation_email("Your application to Appfolio has been received")
        assert not is_confirmation_email("Weekly newsletter from LinkedIn")

    def test_matches_and_confirms_known_application(self, db_path):
        opp = self._seed(db_path, "acme")
        llm = FakeLLMPort({"acme": '{"confirmed": true, "company": "acme"}'})
        chat = FakeChatPort()
        port = FakeEmailPort([
            {"subject": "Your application to Acme was received", "body": "",
             "from": "no-reply@acme.com", "date": ""}])
        confirmed, skipped = ingest_email_confirmations(db_path, port, chat, llm)
        assert (confirmed, skipped) == (1, 0)
        with cursor(db_path) as cur:
            assert cur.execute("SELECT status FROM opportunities WHERE id=?",
                               (opp,)).fetchone()["status"] == "confirmed"
        assert any("Acme" in p["text"] or "acme" in p["text"] for p in chat.posts)

    def test_marketing_email_never_matches_or_logs(self, db_path):
        self._seed(db_path, "acme")
        llm = FakeLLMPort({})  # should never even be consulted
        port = FakeEmailPort([
            {"subject": "Your HELOC application — you may be eligible",
             "body": "Flexible access to your home equity!", "from": "promo@bank.com",
             "date": ""}])
        confirmed, skipped = ingest_email_confirmations(db_path, port, FakeChatPort(), llm)
        assert confirmed == 0
        # no new opportunity created from the marketing mail
        with cursor(db_path) as cur:
            assert cur.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"] == 1

    def test_llm_gate_rejects_non_confirmation_for_known_company(self, db_path):
        # Email mentions a known company but the LLM says it's not a confirmation
        # (e.g. a rejection) -> not confirmed, status unchanged.
        opp = self._seed(db_path, "acme")
        llm = FakeLLMPort({"acme": '{"confirmed": false, "company": null}'})
        port = FakeEmailPort([
            {"subject": "Update on your application to Acme",
             "body": "Unfortunately we are not moving forward.", "from": "hr@acme.com",
             "date": ""}])
        confirmed, skipped = ingest_email_confirmations(db_path, port, FakeChatPort(), llm)
        assert (confirmed, skipped) == (0, 1)
        with cursor(db_path) as cur:
            assert cur.execute("SELECT status FROM opportunities WHERE id=?",
                               (opp,)).fetchone()["status"] == "sourced"

    def test_no_llm_confirms_nothing(self, db_path):
        self._seed(db_path, "acme")
        port = FakeEmailPort([
            {"subject": "Your application to Acme was received", "body": "",
             "from": "no-reply@acme.com", "date": ""}])
        confirmed, skipped = ingest_email_confirmations(db_path, port, FakeChatPort(), None)
        assert confirmed == 0  # fail safe without the LLM gate

    def test_never_creates_new_opportunity(self, db_path):
        # A confirmation for a company Josh never applied to -> no match -> ignored.
        llm = FakeLLMPort({"ghostco": '{"confirmed": true, "company": "ghostco"}'})
        port = FakeEmailPort([
            {"subject": "Your application to GhostCo was received", "body": "",
             "from": "no-reply@ghostco.com", "date": ""}])
        confirmed, skipped = ingest_email_confirmations(db_path, port, FakeChatPort(), llm)
        assert confirmed == 0
        with cursor(db_path) as cur:
            assert cur.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"] == 0

    def test_idempotent_already_confirmed(self, db_path):
        opp = self._seed(db_path, "acme")
        with cursor(db_path) as cur:
            cur.execute("UPDATE opportunities SET status='confirmed' WHERE id=?", (opp,))
        llm = FakeLLMPort({"acme": '{"confirmed": true, "company": "acme"}'})
        port = FakeEmailPort([
            {"subject": "Your application to Acme was received", "body": "",
             "from": "no-reply@acme.com", "date": ""}])
        confirmed, skipped = ingest_email_confirmations(db_path, port, FakeChatPort(), llm)
        assert confirmed == 0  # already confirmed -> skipped, no double count

    def test_receipt_has_role_posting_and_email_links(self, db_path):
        record_opportunity(db_path, "Enterprise AE", "simplify", 60, tier="B",
                           company_normalized="acme", status="sourced",
                           source_url="https://boards.greenhouse.io/acme/jobs/9")
        llm = FakeLLMPort({"acme": '{"confirmed": true, "company": "acme"}'})
        chat = FakeChatPort()
        port = FakeEmailPort([
            {"subject": "Your application to Acme was received", "body": "",
             "from": "no-reply@acme.com", "date": "",
             "message_id": "<abc123@acme.com>"}])
        ingest_email_confirmations(db_path, port, chat, llm)
        text = chat.posts[0]["text"]
        assert "Enterprise AE" in text
        assert "boards.greenhouse.io/acme/jobs/9" in text        # posting link
        assert "rfc822msgid:abc123" in text                       # gmail deep-link
        assert "who do I know at Acme" in text                     # next-step nudge

    def test_receipt_falls_back_to_linkedin_when_no_url(self, db_path):
        record_opportunity(db_path, "Head of Growth", "simplify", 60, tier="B",
                           company_normalized="beta", status="sourced")  # no source_url
        llm = FakeLLMPort({"beta": '{"confirmed": true, "company": "beta"}'})
        chat = FakeChatPort()
        port = FakeEmailPort([
            {"subject": "Your application to Beta received", "body": "",
             "from": "no-reply@beta.com", "date": "", "message_id": ""}])
        ingest_email_confirmations(db_path, port, chat, llm)
        assert "linkedin.com/jobs/search" in chat.posts[0]["text"]

    def test_fake_port_returns_messages(self):
        port = FakeEmailPort([{"subject": "Application received — Acme", "body": ""}])
        assert len(port.get_confirmations()) == 1

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

    def test_intake_job_confirms_via_live_port(self, db_path, monkeypatch):
        # With creds set, the job builds the IMAP port + LLM and confirms a
        # matching, already-tracked application.
        from banks import jobs
        from banks.config import BanksConfig
        record_opportunity(db_path, "AE", "simplify", 60, tier="B",
                           company_normalized="appfolio", status="sourced")
        monkeypatch.setattr(
            "banks.config.load_config",
            lambda: BanksConfig(None, None, intake_email="jbkantor@gmail.com",
                                intake_email_password="app-pw"))
        monkeypatch.setattr(
            "banks.emailport.LiveImapEmailPort",
            lambda email, pw: FakeEmailPort([
                {"subject": "Your application to AppFolio was received",
                 "body": "", "from": "no-reply@appfolio.com", "date": ""}]))
        monkeypatch.setattr(
            "banks.llmport.load_llm_port",
            lambda: FakeLLMPort({"appfolio": '{"confirmed": true, "company": "appfolio"}'}))
        result = jobs.run_job("email_intake_poll", db_path, FakeChatPort())
        assert result == {"ingested": 1, "skipped": 0}
