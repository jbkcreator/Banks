"""MOD-01/02 intake orchestration tests (Decisions 4, 5, 6).

Uses FakeCSVPort/FakeChatPort — no real files, no network.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.csvport import (FakeCSVPort, parse_alumni_row,
                           parse_linkedin_connection_row, parse_recruiter_row)
from banks.exclusion import add_company_exclusion
from banks.intake import (export_enrichment_queue, ingest_contacts,
                          ingest_simplify)
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.db")
    init_db(p)
    return p


def _simplify_rows():
    return [
        {"Job Title": "Account Executive", "Company Name": "Acme Inc.",
         "Location": "Remote in USA", "Job URL": "https://x.com/1",
         "Applied Date": "2026-08-24", "Status": "APPLIED", "job_type": "Full-time"},
        {"Job Title": "AE", "Company Name": "Rent Solutions",  # excluded
         "Location": "Tampa, FL", "Job URL": "https://x.com/2",
         "Status": "APPLIED", "job_type": "Full-time"},
        {"Job Title": "Account Executive", "Company Name": "Acme",  # fuzzy dup of #1
         "Location": "Remote", "Job URL": "https://x.com/3",
         "Status": "APPLIED", "job_type": "Full-time"},
    ]


# --- Decision 4: hold half-blind rows, don't flood Slack --------------------

def test_simplify_rows_held_not_surfaced(db_path):
    add_company_exclusion(db_path, "Rent Solutions", "client exclusion")
    chat = FakeChatPort()
    res = ingest_simplify(db_path, FakeCSVPort(_simplify_rows()), "ignored.csv", chat)
    assert res.excluded == 1          # Rent Solutions
    assert res.duplicates == 1        # Acme fuzzy dup
    assert res.ingested == 1          # only the first Acme row
    assert res.held == 1              # held for enrichment
    assert res.surfaced == 0
    assert chat.posts == []           # nothing flooded to Slack


def test_held_rows_flagged_needs_enrichment(db_path):
    ingest_simplify(db_path, FakeCSVPort(_simplify_rows()[:1]), "x", FakeChatPort())
    with cursor(db_path) as cur:
        row = cur.execute("SELECT needs_enrichment, source_url FROM opportunities").fetchone()
    assert row["needs_enrichment"] == 1
    assert row["source_url"] == "https://x.com/1"   # dedup Pass 1 populated


# --- Decision 6: enrichment queue export ------------------------------------

def test_export_enrichment_queue(db_path, tmp_path):
    ingest_simplify(db_path, FakeCSVPort(_simplify_rows()[:1]), "x", FakeChatPort())
    out = tmp_path / "needs.csv"
    n = export_enrichment_queue(db_path, str(out))
    assert n == 1
    assert "title,company,source_url" in out.read_text(encoding="utf-8")


# --- Decision 5: merge contacts, upgrade source, backfill fields ------------

def test_contact_merge_upgrades_source_and_backfills(db_path):
    url = "https://linkedin.com/in/jane"
    linkedin = [{"First Name": "Jane", "Last Name": "Doe", "URL": url,
                 "Email Address": "jane@x.com", "Company": "Acme",
                 "Position": "VP Sales", "Connected On": "01 Jan 2026"}]
    recruiter = [{"First Name": "Jane", "Last Name": "Doe", "Title": "Partner",
                  "Company": "Acme", "Vertical Fit": "GTM/SaaS",
                  "LinkedIn URL": url, "Notes": "warm - call booked"}]

    ins1, mrg1 = ingest_contacts(db_path, FakeCSVPort(linkedin), "x", parse_linkedin_connection_row)
    ins2, mrg2 = ingest_contacts(db_path, FakeCSVPort(recruiter), "x", parse_recruiter_row)
    assert (ins1, mrg1) == (1, 0)
    assert (ins2, mrg2) == (0, 1)     # merged, not a second row

    with cursor(db_path) as cur:
        rows = cur.execute("SELECT * FROM contacts").fetchall()
    assert len(rows) == 1             # still one person
    r = rows[0]
    assert r["source"] == "recruiter_registry"   # upgraded from linkedin_csv
    assert r["vertical_fit"] == "GTM/SaaS"        # backfilled
    assert r["notes"] == "warm - call booked"
    assert r["email"] == "jane@x.com"             # kept from linkedin row


# --- MOD-01 <-> MOD-02 join: warm-path attaches on surface --------------------

def test_surface_attaches_warm_contact(db_path):
    from banks.warmpath import find_warm_contacts
    from banks.manual_intake import ingest_manual
    from banks.llmport import FakeLLMPort
    # seed a contact at the target company
    linkedin = [{"First Name": "Meghan", "Last Name": "Overheim",
                 "URL": "https://linkedin.com/in/meghan", "Email Address": "",
                 "Company": "Second Nature", "Position": "Account Executive",
                 "Connected On": ""}]
    ingest_contacts(db_path, FakeCSVPort(linkedin), "x", parse_linkedin_connection_row)

    assert find_warm_contacts(db_path, "Second Nature")[0]["name"] == "Meghan Overheim"

    jd = "Head of Onboarding at Second Nature. Remote. PropTech. Base salary $230,000."
    llm = FakeLLMPort({"onboarding": (
        '{"title":"Head of Onboarding","company":"Second Nature",'
        '"location":"Remote","industry":"PropTech"}')})
    res = ingest_manual(db_path, FakeChatPort(), jd_text=jd, llm=llm)
    assert res.surfaced is True
    with cursor(db_path) as cur:
        cid = cur.execute("SELECT contact_id FROM opportunities WHERE id=?",
                          (res.opportunity_id,)).fetchone()["contact_id"]
        name = cur.execute("SELECT name FROM contacts WHERE id=?", (cid,)).fetchone()["name"]
    assert name == "Meghan Overheim"   # warm contact attached to the opportunity


def test_referral_path_surfaces_recruiter_by_vertical(db_path):
    """No direct contact at the company, but a recruiter covers the vertical."""
    from banks.warmpath import find_referral_paths
    recruiter = [{"First Name": "Tabitha", "Last Name": "Francis", "Title": "Global Director",
                  "Company": "LMRE", "Vertical Fit": "PropTech/Real Estate Tech",
                  "LinkedIn URL": "https://li/tabitha", "Notes": "warm"}]
    ingest_contacts(db_path, FakeCSVPort(recruiter), "x", parse_recruiter_row)

    # cold company "Vibes", but role industry is PropTech -> recruiter is the avenue
    paths = find_referral_paths(db_path, "Vibes", industry="PropTech")
    assert any(p["path"] == "recruiter" and p["name"] == "Tabitha Francis" for p in paths)


def test_no_warm_contact_still_surfaces(db_path):
    from banks.manual_intake import ingest_manual
    from banks.llmport import FakeLLMPort
    jd = "VP Sales at Unknownco. Remote. SaaS. Base salary $230,000."
    llm = FakeLLMPort({"unknownco": (
        '{"title":"VP Sales","company":"Unknownco","location":"Remote","industry":"SaaS"}')})
    res = ingest_manual(db_path, FakeChatPort(), jd_text=jd, llm=llm)
    assert res.surfaced is True        # surfaces even with no known contacts
    with cursor(db_path) as cur:
        cid = cur.execute("SELECT contact_id FROM opportunities WHERE id=?",
                          (res.opportunity_id,)).fetchone()["contact_id"]
    assert cid is None


def test_contact_no_downgrade(db_path):
    """A later linkedin_csv row must not clobber a recruiter label."""
    url = "https://linkedin.com/in/j"
    recruiter = [{"First Name": "J", "Last Name": "D", "Title": "Partner",
                  "Company": "Acme", "Vertical Fit": "GTM", "LinkedIn URL": url,
                  "Notes": "n"}]
    linkedin = [{"First Name": "J", "Last Name": "D", "URL": url,
                 "Email Address": "", "Company": "Acme", "Position": "P",
                 "Connected On": ""}]
    ingest_contacts(db_path, FakeCSVPort(recruiter), "x", parse_recruiter_row)
    ingest_contacts(db_path, FakeCSVPort(linkedin), "x", parse_linkedin_connection_row)
    with cursor(db_path) as cur:
        r = cur.execute("SELECT source FROM contacts").fetchone()
    assert r["source"] == "recruiter_registry"
