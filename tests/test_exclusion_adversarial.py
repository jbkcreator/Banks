"""MOD-06 adversarial exclusion suite — prove the sneaky paths are blocked at
BOTH gates, and the one deliberate non-block (moved-on ex-employee) works.
"""
from __future__ import annotations

import datetime as dt

import pytest

from banks.exclusion import (
    add_company_exclusion,
    add_person_exclusion,
    is_company_excluded,
    is_conduit_excluded,
    is_contact_excluded,
    is_indirectly_excluded,
    is_person_excluded,
    load_exclusions_from_file,
)
from banks.mailer import FakeMailer
from banks.relay import relay_run
from banks.store import cursor, init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _approved_intent(db, ref, *, company=None, contact=None):
    """Wire a minimal opportunity+lane+contact+approved send_intent for `ref`."""
    with cursor(db) as cur:
        opp_id = None
        if company is not None:
            cur.execute(
                "INSERT INTO opportunities (title, source, company_normalized, tier) "
                "VALUES ('VP Sales', 'simplify', ?, 'A')", (company,))
            opp_id = cur.lastrowid
        contact_id = None
        if contact is not None:
            cur.execute(
                "INSERT INTO contacts (name, company, linkedin_url, email, degree, "
                "source, verified, added_at) VALUES (?,?,?,?,1,'linkedin_csv',1,?)",
                (contact.get("name"), contact.get("company"),
                 contact.get("linkedin_url"), contact.get("email", "x@y.com"), _now()))
            contact_id = cur.lastrowid
        cur.execute(
            "INSERT INTO outreach_lanes (opportunity_id, lane_type, contact_id, "
            "draft_ref, status, created_at) VALUES (?, 'hiring_manager', ?, ?, 'pending', ?)",
            (opp_id, contact_id, ref, _now()))
        cur.execute(
            "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, "
            "status, created_at) VALUES (?, 'email:sendas', 'a@b.com', 's', 'b', 'approved', ?)",
            (ref, _now()))


# 1 — company casing / suffix / whitespace variants
@pytest.mark.parametrize("variant", [
    "Rent Solutions", "rent solutions", "RENT SOLUTIONS",
    "Rent Solutions, LLC", "Rent Solutions Inc.", "rent  solutions",
])
def test_company_variants_all_blocked(db, variant):
    add_company_exclusion(db, "Rent Solutions")
    assert is_company_excluded(db, variant)


# 2 — excluded person survives a job move (linkedin key)
def test_person_blocked_after_job_move(db):
    add_person_exclusion(db, linkedin_url="https://linkedin.com/in/jane")
    # same human, new company + new email — still excluded by linkedin_url
    assert is_contact_excluded(db, {
        "name": "Jane Doe", "company": "beta",
        "linkedin_url": "https://linkedin.com/in/jane", "email": "jane@beta.com"})


def test_person_blocked_by_name_when_no_linkedin(db):
    add_person_exclusion(db, name="Jane Doe")
    assert is_person_excluded(db, name="jane  doe")   # normalised match


# 3 — indirect: a warm-intro conduit at the excluded firm is blocked
def test_conduit_at_excluded_firm_blocked(db):
    add_company_exclusion(db, "Rent Solutions")
    conduit = {"name": "Bob", "company": "rent solutions",
               "linkedin_url": "https://linkedin.com/in/bob"}
    assert is_conduit_excluded(db, conduit)


# 4 — send-time race: queued before exclusion, excluded, then relay blocks it
def test_send_time_gate_blocks_post_queue_exclusion(db):
    _approved_intent(db, "1", company="acme")
    add_company_exclusion(db, "Acme")          # excluded AFTER the intent was approved
    res = relay_run(db, FakeMailer())
    assert "1" in res.blocked
    assert res.sent == []
    with cursor(db) as cur:
        st = cur.execute("SELECT status FROM send_intents WHERE draft_ref='1'").fetchone()["status"]
    assert st == "suppressed"


def test_send_time_gate_blocks_excluded_person(db):
    add_person_exclusion(db, linkedin_url="https://linkedin.com/in/jane")
    _approved_intent(db, "2", company="cleanco",
                     contact={"name": "Jane", "company": "cleanco",
                              "linkedin_url": "https://linkedin.com/in/jane"})
    res = relay_run(db, FakeMailer())
    assert "2" in res.blocked and res.sent == []


# 5 — corporate substring variant
def test_corporate_substring_indirectly_excluded(db):
    add_company_exclusion(db, "Rent Solutions")
    assert is_indirectly_excluded(db, "Rent Solutions Holdings")
    assert is_indirectly_excluded(db, "Rent Solutions Group, LLC")


# 6 — NEGATIVE control: moved-on ex-employee is still contactable
def test_moved_on_ex_employee_not_blocked(db):
    add_company_exclusion(db, "Rent Solutions")
    # person now at a non-excluded company, not personally excluded
    contact = {"name": "Carol", "company": "goodco",
               "linkedin_url": "https://linkedin.com/in/carol"}
    assert not is_contact_excluded(db, contact)
    assert not is_conduit_excluded(db, contact)


def test_clean_send_passes(db):
    _approved_intent(db, "3", company="cleanco",
                     contact={"name": "Dave", "company": "cleanco",
                              "linkedin_url": "https://linkedin.com/in/dave"})
    res = relay_run(db, FakeMailer())
    assert res.sent == ["3"] and res.blocked == []


# seed-file loader
def test_load_exclusions_from_file(db, tmp_path):
    p = tmp_path / "excl.txt"
    p.write_text(
        "# comment\ncompany: Rent Solutions\nperson: Jane Doe\n"
        "person: https://linkedin.com/in/bob\n\n",
        encoding="utf-8")
    counts = load_exclusions_from_file(db, str(p))
    assert counts == {"companies": 1, "people": 2}
    assert is_company_excluded(db, "Rent Solutions, LLC")
    assert is_person_excluded(db, name="Jane Doe")
    assert is_person_excluded(db, linkedin_url="https://linkedin.com/in/bob")
    # idempotent
    load_exclusions_from_file(db, str(p))
    from banks.exclusion import list_person_exclusions
    assert len(list_person_exclusions(db)) == 2
