"""URL-enrichment tests: held opportunity -> fetch posting -> re-score -> surface."""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.csvport import FakeCSVPort
from banks.enrich import (EnrichResult, FakeFetchPort, enrich_opportunity,
                          enrich_pending, html_to_text)
from banks.intake import ingest_simplify
from banks.llmport import FakeLLMPort
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _simplify_row(url):
    return {"Job Title": "VP of Sales", "Company Name": "AppFolio",
            "Location": "", "Job URL": url, "Status": "APPLIED",
            "job_type": "Full-time"}


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><style>x{}</style><body>Base salary <b>$230,000</b><script>a()</script></body></html>"
    assert "Base salary $230,000" in html_to_text(html)
    assert "x{}" not in html_to_text(html) and "a()" not in html_to_text(html)


def test_enrich_surfaces_held_row(db_path):
    url = "https://jobs.example.com/appfolio/vp-sales"
    # 1) ingest via Simplify -> held (no comp/industry)
    ingest_simplify(db_path, FakeCSVPort([_simplify_row(url)]), "x", FakeChatPort())
    with cursor(db_path) as cur:
        oid = cur.execute("SELECT id, needs_enrichment FROM opportunities").fetchone()
    assert oid["needs_enrichment"] == 1

    # 2) enrich from the posting page
    fetch = FakeFetchPort({url: "VP of Sales at AppFolio. Remote in USA. "
                                "Base salary $230,000. AppFolio is a PropTech SaaS company."})
    llm = FakeLLMPort({"appfolio": (
        '{"title":"VP of Sales","company":"AppFolio","location":"Remote in USA",'
        '"industry":"PropTech"}')})
    chat = FakeChatPort()
    res = enrich_opportunity(db_path, oid["id"], fetch, llm, chat)

    assert res.outcome == "surfaced"
    assert res.tier == "A"
    assert len(chat.posts) == 1
    with cursor(db_path) as cur:
        row = cur.execute("SELECT tier, needs_enrichment, status FROM opportunities").fetchone()
    assert row["needs_enrichment"] == 0 and row["tier"] == "A" and row["status"] == "drafted"


def test_enrich_fetch_failure_keeps_held(db_path):
    url = "https://dead.example.com/x"
    ingest_simplify(db_path, FakeCSVPort([_simplify_row(url)]), "x", FakeChatPort())
    with cursor(db_path) as cur:
        oid = cur.execute("SELECT id FROM opportunities").fetchone()["id"]
    fetch = FakeFetchPort({})  # returns None -> fetch failed
    res = enrich_opportunity(db_path, oid, fetch, FakeLLMPort(), FakeChatPort())
    assert res.outcome == "fetch_failed"
    with cursor(db_path) as cur:
        assert cur.execute("SELECT needs_enrichment FROM opportunities").fetchone()["needs_enrichment"] == 1


def test_enrich_pending_batch(db_path):
    url = "https://jobs.example.com/appfolio/vp-sales"
    ingest_simplify(db_path, FakeCSVPort([_simplify_row(url)]), "x", FakeChatPort())
    fetch = FakeFetchPort({url: "VP of Sales. Remote. Base salary $230,000. PropTech SaaS."})
    llm = FakeLLMPort({"vp of sales": (
        '{"title":"VP of Sales","company":"AppFolio","location":"Remote","industry":"PropTech"}')})
    batch = enrich_pending(db_path, fetch, llm, FakeChatPort())
    assert batch.processed == 1 and batch.surfaced == 1


# --- PR #7 regression: LinkedIn-only enrichment result -------------------------

def test_linkedin_only_result_persisted(db_path):
    """Result with LinkedIn URL but no email is stored with verified=0."""
    from banks.contact_enrichment import (
        EnrichmentResult, FakeEnrichmentPort, enqueue_company,
        submit_pending, retrieve_and_apply,
    )
    enqueue_company(db_path, "linkedinco", role_hint=None, opportunity_id=None)
    port = FakeEnrichmentPort({"linkedinco": [EnrichmentResult(
        company="linkedinco", name="Alex Kim",
        email="", linkedin_url="https://linkedin.com/in/alexkim",
        title="VP Sales", verified=False,
    )]})
    batch_id = submit_pending(db_path, port)
    written = retrieve_and_apply(db_path, port, batch_id)
    assert written == 1
    with cursor(db_path) as cur:
        c = cur.execute("SELECT linkedin_url, verified FROM contacts WHERE company='linkedinco'").fetchone()
        q = cur.execute("SELECT status FROM enrichment_queue WHERE batch_id=?", (batch_id,)).fetchone()
    assert c["linkedin_url"] == "https://linkedin.com/in/alexkim"
    assert c["verified"] == 0
    assert q["status"] == "done"


def test_no_usable_result_marks_failed(db_path):
    """No email and no LinkedIn URL marks queue row failed, not done."""
    from banks.contact_enrichment import (
        EnrichmentResult, FakeEnrichmentPort, enqueue_company,
        submit_pending, retrieve_and_apply,
    )
    enqueue_company(db_path, "ghostco", role_hint=None, opportunity_id=None)
    port = FakeEnrichmentPort({"ghostco": [EnrichmentResult(
        company="ghostco", name="", email="", linkedin_url="", title="", verified=False,
    )]})
    batch_id = submit_pending(db_path, port)
    written = retrieve_and_apply(db_path, port, batch_id)
    assert written == 0
    with cursor(db_path) as cur:
        q = cur.execute("SELECT status FROM enrichment_queue WHERE batch_id=?", (batch_id,)).fetchone()
    assert q["status"] == "failed"
