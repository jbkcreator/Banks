"""Read-only job-search inbox view (approved 2026-09-02).

The load-bearing test in this file is `test_unrelated_mail_is_dropped`: Josh
granted read access to his personal Gmail, so the filter is the only thing
standing between his private mail and an LLM prompt / a Slack channel. If that
test ever goes red, the feature is a privacy incident, not a bug.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from banks.emailport import FakeEmailPort
from banks.inbox import (clear_cache, format_job_mail, is_job_related,
                         recent_job_mail, _sender_domain)
from banks.store import init_db


@pytest.fixture(autouse=True)
def _no_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO opportunities (title, company_normalized, "
                  "source, status) VALUES (?,?,?,?)",
                  ("AE", "evolve", "simplify", "applied"))
        c.execute("INSERT INTO contacts (name, company, source, added_at) "
                  "VALUES (?,?,?,?)", ("Tabitha Francis", "lmre", "recruiter_registry", "2026-09-01"))
    return path


def _msg(frm, subject, body=""):
    return {"from": frm, "subject": subject, "body": body, "date": "Tue, 2 Sep 2026"}


# ---------------------------------------------------------------------------
# The filter

def test_ats_sender_is_job_related():
    assert is_job_related(
        _msg("no-reply@greenhouse.io", "Thanks for applying"), set(), set())


def test_ats_subdomain_is_job_related():
    assert is_job_related(
        _msg("x@mail.lever.co", "Your application"), set(), set())


def test_tracked_company_in_subject_is_job_related():
    assert is_job_related(
        _msg("someone@random.com", "Update on your Evolve application"),
        {"evolve"}, set())


def test_tracked_company_in_sender_domain_is_job_related():
    assert is_job_related(
        _msg("recruiting@evolve.com", "Your application"), {"evolve"}, set())


def test_tracked_company_mailing_about_something_else_is_dropped():
    """PadSplit is a tracked opportunity, but its marketplace also sends rental
    notices. Same domain, not about his application. Seen live 2026-09-02."""
    assert not is_job_related(
        _msg("PadSplit Messenger <messenger@padsplit.com>",
             "Message from Randy at 5207 Cillette Avenue, North Port",
             "View your message"),
        {"padsplit"}, set())


def test_known_contact_not_tied_to_applied_job_is_dropped():
    """Scope is applied jobs only (client instruction 2026-09-02). A recruiter
    Josh knows, writing about a role he never applied to, is out of scope."""
    assert not is_job_related(
        _msg("Tabitha Francis <t.francis@lmre.tech>", "Re: a VP Sales role",
             "Wanted to follow up on the interview"),
        set(), {"francis"})


def test_known_contact_about_applied_company_is_kept():
    """...but the same recruiter writing about a company he DID apply to is in."""
    assert is_job_related(
        _msg("Tabitha Francis <t.francis@lmre.tech>", "Update on your Evolve application"),
        {"evolve"}, {"francis"})


def test_job_board_alert_without_applied_company_is_dropped():
    """LinkedIn Job Alerts for roles he never applied to. Seen live 2026-09-02."""
    assert not is_job_related(
        _msg("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
             "Diagnostic Sales Specialist - Tampa, Fl at Hologic, Inc."),
        {"evolve"}, set())


def test_job_board_alert_naming_applied_company_is_kept():
    assert is_job_related(
        _msg("LinkedIn <jobalerts-noreply@linkedin.com>",
             "Your application to Evolve was viewed"), {"evolve"}, set())


def test_marketplace_spam_is_dropped():
    """Upwork blasted 8 of these into the filter on 2026-09-02."""
    assert not is_job_related(
        _msg("Jitender K. via Upwork <room_cb11@email.upwork.com>",
             "Jitender K. sent you a message", "View your message and send a reply"),
        {"evolve"}, set())


def test_surname_substring_cannot_match_a_mail_host():
    """Contact 'Mai' must not match 'e-MAIl@MAIl.variety.com' — that alone
    passed 11 newsletters live on 2026-09-02."""
    assert not is_job_related(
        _msg("Variety Breaking News <email@mail.variety.com>",
             "Gal Gadot is a mother on a mission", "review of the new job"),
        {"evolve"}, {"mai"})


@pytest.mark.parametrize("msg", [
    _msg("statements@chase.com", "Your monthly statement is ready"),
    _msg("noreply@mychart.org", "New lab results available"),
    _msg("mom@family.com", "call me back"),
    _msg("deals@retailer.com", "50% off everything this weekend"),
    _msg("newsletter@substack.com", "The Monday memo"),
])
def test_unrelated_mail_is_dropped(msg):
    """Private mail must never reach an LLM prompt or Slack. Load-bearing."""
    assert not is_job_related(msg, {"evolve"}, {"francis"})


def test_short_company_slug_cannot_match_everything():
    """A 2-char slug must not act as a wildcard against every subject."""
    assert not is_job_related(_msg("a@b.com", "lunch"), {"hp", ""}, set())


def test_sender_domain_parsing():
    assert _sender_domain("A B <x@Mail.Acme.COM>") == "mail.acme.com"
    assert _sender_domain("garbage") == ""


# ---------------------------------------------------------------------------
# End to end through the port

def test_recent_job_mail_filters_against_db(db):
    port = FakeEmailPort([], recent=[
        _msg("no-reply@greenhouse.io", "Application received — Evolve"),
        _msg("statements@chase.com", "Your monthly statement"),
        _msg("mom@family.com", "call me"),
    ])
    out = recent_job_mail(db, port, days=14)
    assert len(out) == 1
    assert "Evolve" in out[0]["subject"]
    rendered = format_job_mail(out)
    assert "chase" not in rendered.lower()
    assert "mom@family.com" not in rendered


def test_recent_job_mail_returns_snippet_not_full_body(db):
    port = FakeEmailPort([], recent=[
        _msg("no-reply@lever.co", "Your application", "x" * 5000)])
    out = recent_job_mail(db, port, days=14)
    assert len(out[0]["snippet"]) < 300


def test_empty_inbox_message_is_honest(db):
    assert "actually applied to" in format_job_mail([], days=14)


def test_cache_avoids_a_second_imap_login(db):
    """A repeat question in one Slack thread must not re-hit IMAP."""
    class CountingPort:
        calls = 0
        def fetch_recent(self, days=14):
            CountingPort.calls += 1
            return [_msg("no-reply@greenhouse.io", "Application received — Evolve")]
    port = CountingPort()
    a = recent_job_mail(db, port, days=14)
    b = recent_job_mail(db, port, days=14)
    assert CountingPort.calls == 1
    assert a == b


def test_cache_can_be_bypassed(db):
    class CountingPort:
        calls = 0
        def fetch_recent(self, days=14):
            CountingPort.calls += 1
            return []
    port = CountingPort()
    recent_job_mail(db, port, days=14, use_cache=False)
    recent_job_mail(db, port, days=14, use_cache=False)
    assert CountingPort.calls == 2


def test_cache_expires(db, monkeypatch):
    import banks.inbox as inbox
    class CountingPort:
        calls = 0
        def fetch_recent(self, days=14):
            CountingPort.calls += 1
            return []
    port = CountingPort()
    recent_job_mail(db, port, days=14)
    # Capture the real clock BEFORE patching — inbox.time is the stdlib module,
    # so a lambda calling time.monotonic() would recurse into itself.
    later = time.monotonic() + inbox.CACHE_TTL_S + 1
    monkeypatch.setattr(inbox.time, "monotonic", lambda: later)
    recent_job_mail(db, port, days=14)
    assert CountingPort.calls == 2


def test_cache_never_touches_disk(db):
    """The cache is in-memory only — Josh's mail must not land in the DB."""
    import banks.inbox as inbox
    recent_job_mail(db, FakeEmailPort([], recent=[
        _msg("no-reply@greenhouse.io", "Application received — Evolve")]), days=14)
    assert inbox._cache, "expected an in-memory entry"
    with sqlite3.connect(db) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            rows = c.execute(f'SELECT * FROM "{t}"').fetchall()
            assert not any("greenhouse" in str(r).lower() for r in rows), t


def test_no_writes_to_db(db):
    """The tool is read-only — row counts must be untouched."""
    def counts():
        with sqlite3.connect(db) as c:
            return (c.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0])
    before = counts()
    recent_job_mail(db, FakeEmailPort([], recent=[
        _msg("no-reply@greenhouse.io", "Application received — Evolve")]), days=14)
    assert counts() == before


def test_company_slug_substring_inside_an_unrelated_word_is_dropped():
    """'angi' (a real tracked company) matched inside 'ch-ANGI-ng' in an
    unrelated newsletter subject and let it through the filter live on
    2026-09-02 — same failure mode as the surname substring bug, different
    field. Word-boundary match closes both."""
    assert not is_job_related(
        _msg("Podfest Messenger <podfest-messenger@mail.beehiiv.com>",
             "YouTube Is Changing What Counts as a View", "creator economy news"),
        {"angi", "evolve"}, set())


def test_company_slug_as_a_whole_word_still_matches():
    assert is_job_related(
        _msg("someone@random.com", "Update on your Angi application",
             "we reviewed your application"),
        {"angi", "evolve"}, set())
