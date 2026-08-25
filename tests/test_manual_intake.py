"""Manual Intake Surface tests (MOD-01 plan line 98).

Covers the comp extractor and the three entry modes: full JD (fully scored,
can surface), URL-only and quick input (held for enrichment).
"""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.exclusion import add_company_exclusion
from banks.llmport import FakeLLMPort
from banks.manual_intake import extract_comp_k, ingest_manual
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


# --- comp extractor ---------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Base salary: $150,000 per year", 150.0),
    ("Compensation is $220k", 220.0),
    ("OTE $180,000 - $240,000", 180.0),          # lower bound
    ("base pay 150K–200K", 150.0),
    ("We have 500 employees, founded 2019", None),  # no comp context
    ("Salary range $90,000 to $110,000", 90.0),
    ("Salary range $95,000", 95.0),                 # regression: was dropped
    ("base 180000", 180.0),                          # bare thousands
    ("base 150", 150.0),                             # bare 2-3 digit
])
def test_extract_comp_k(text, expected):
    assert extract_comp_k(text) == expected


# --- full JD -> fully scored, can surface -----------------------------------

def test_full_jd_scores_and_surfaces(db_path):
    llm = FakeLLMPort({"onboarding": (
        '{"title":"Head of Onboarding","company":"Second Nature",'
        '"location":"Remote in USA","industry":"PropTech"}'
    )})
    jd = ("Head of Onboarding at Second Nature. Remote in USA. "
          "PropTech SaaS. Base salary: $230,000. Lead onboarding org.")
    chat = FakeChatPort()
    res = ingest_manual(db_path, chat, jd_text=jd, llm=llm)

    assert res.needs_enrichment is False        # comp + industry known
    assert res.tier == "A"                       # $230k + PropTech + remote + FT
    assert res.surfaced is True
    assert len(chat.posts) == 1
    with cursor(db_path) as cur:
        row = cur.execute("SELECT tier, needs_enrichment, source FROM opportunities").fetchone()
    assert row["tier"] == "A" and row["needs_enrichment"] == 0 and row["source"] == "manual"


# --- URL / quick input -> held for enrichment -------------------------------

def test_url_only_held(db_path):
    chat = FakeChatPort()
    res = ingest_manual(db_path, chat, url="https://x.com/job",
                        title="AE", company="Acme")
    assert res.needs_enrichment is True
    assert res.surfaced is False
    assert chat.posts == []


def test_quick_applied_here_held(db_path):
    res = ingest_manual(db_path, FakeChatPort(), title="SDR", company="Ketch")
    assert res.needs_enrichment is True
    with cursor(db_path) as cur:
        n = cur.execute("SELECT COUNT(*) n FROM opportunities WHERE needs_enrichment=1").fetchone()["n"]
    assert n == 1


# --- gates ------------------------------------------------------------------

def test_manual_respects_exclusion(db_path):
    add_company_exclusion(db_path, "Rent Solutions")
    res = ingest_manual(db_path, FakeChatPort(), title="AE", company="Rent Solutions, LLC")
    assert res.skipped == "excluded"


def test_manual_dedup(db_path):
    ingest_manual(db_path, FakeChatPort(), url="https://x.com/1", title="AE", company="Acme")
    res = ingest_manual(db_path, FakeChatPort(), url="https://x.com/1", title="AE", company="Acme")
    assert res.skipped == "duplicate"


def test_manual_needs_title_and_company(db_path):
    with pytest.raises(ValueError):
        ingest_manual(db_path, FakeChatPort(), title="AE")
