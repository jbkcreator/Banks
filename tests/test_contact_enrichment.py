"""Verified contact enrichment (MOD-02) — queue -> batch -> apply."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from banks.chatport import FakeChatPort
from banks.contact_enrichment import (EnrichmentRequest, EnrichmentResult,
                                      FakeEnrichmentPort, ManualCSVEnrichmentPort,
                                      enqueue_company, has_fresh_enrichment,
                                      retrieve_and_apply, submit_pending)
from banks.csvport import FakeCSVPort, parse_linkedin_connection_row
from banks.intake import ingest_contacts
from banks.store import cursor, init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _seed_opp(db_path, company_norm, oid_title="VP Sales"):
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO opportunities (title, source, criteria_match_score, tier, "
            "company_normalized) VALUES (?, 'manual', 90, 'A', ?)",
            (oid_title, company_norm))
        return cur.lastrowid


# --- queue ------------------------------------------------------------------

def test_enqueue_cold_company(db_path):
    oid = _seed_opp(db_path, "vibes")
    assert enqueue_company(db_path, "vibes", "VP Sales", oid) is True
    # duplicate pending -> not re-enqueued
    assert enqueue_company(db_path, "vibes", "VP Sales", oid) is False


def test_enqueue_skips_fresh_cache(db_path):
    # a fresh clay_enrichment contact exists -> skip
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, email, degree, source, verified, "
            "enriched_at, added_at) VALUES (?,?,?,1,?,1,?,?)",
            ("X", "vibes", "x@vibes.com", "clay_enrichment",
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
    assert has_fresh_enrichment(db_path, "vibes") is True
    assert enqueue_company(db_path, "vibes", None, None) is False


def test_stale_cache_does_not_block(db_path):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, email, degree, source, verified, "
            "enriched_at, added_at) VALUES (?,?,?,1,?,1,?,?)",
            ("X", "vibes", "x@vibes.com", "clay_enrichment", old, old))
    assert has_fresh_enrichment(db_path, "vibes") is False


# --- batch round trip -------------------------------------------------------

def test_fake_batch_resolves_and_attaches(db_path):
    oid = _seed_opp(db_path, "vibes")
    enqueue_company(db_path, "vibes", "VP Sales", oid)
    port = FakeEnrichmentPort({"vibes": [EnrichmentResult(
        company="vibes", name="Sarah Lee", email="sarah@vibes.com",
        verified=True, title="VP Sales", linkedin_url="https://li/sarah")]})
    bid = submit_pending(db_path, port)
    assert bid is not None
    written = retrieve_and_apply(db_path, port, bid)
    assert written == 1
    with cursor(db_path) as cur:
        c = cur.execute("SELECT name, verified, source FROM contacts WHERE company='vibes'").fetchone()
        assert c["name"] == "Sarah Lee" and c["verified"] == 1 and c["source"] == "clay_enrichment"
        opp = cur.execute("SELECT contact_id FROM opportunities WHERE id=?", (oid,)).fetchone()
        assert opp["contact_id"] is not None            # re-attached
        q = cur.execute("SELECT status FROM enrichment_queue WHERE company_normalized='vibes'").fetchone()
        assert q["status"] == "done"


def test_unverified_result_still_stored_flagged(db_path):
    oid = _seed_opp(db_path, "acme")
    enqueue_company(db_path, "acme", "CRO", oid)
    port = FakeEnrichmentPort({"acme": [EnrichmentResult(
        company="acme", name="Jo", email="jo@acme.com", verified=False, title="CRO")]})
    bid = submit_pending(db_path, port)
    retrieve_and_apply(db_path, port, bid)
    with cursor(db_path) as cur:
        assert cur.execute("SELECT verified FROM contacts WHERE company='acme'").fetchone()["verified"] == 0


def test_submit_empty_queue_returns_none(db_path):
    assert submit_pending(db_path, FakeEnrichmentPort()) is None


# --- manual CSV port --------------------------------------------------------

def test_manual_csv_roundtrip(tmp_path):
    port = ManualCSVEnrichmentPort(out_dir=str(tmp_path))
    bid = port.submit([EnrichmentRequest(company="vibes", role_hint="VP")])
    # pending until a human drops the enriched file back
    assert port.retrieve(bid) is None
    # simulate the returned file
    import csv
    with open(tmp_path / f"enriched_{bid}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["company", "name", "email", "verified", "title", "linkedin_url"])
        w.writerow(["vibes", "Sarah", "sarah@vibes.com", "true", "VP Sales", ""])
    out = port.retrieve(bid)
    assert out and out[0].email == "sarah@vibes.com" and out[0].verified is True


# --- Live Clay port: webhook push + Sheet-buffer pull -----------------------

from banks.config import BanksConfig
from banks.contact_enrichment import (LiveClayEnrichmentPort, drain_submitted,
                                      select_enrichment_port)


def _cfg(**kw) -> BanksConfig:
    # slack_bot_token + slack_channel_id are required positional; default None.
    return BanksConfig(None, None, **kw)


def test_clay_submit_raises_without_webhook():
    # No webhook URL configured → refuse rather than pretend to enrich.
    port = LiveClayEnrichmentPort(_cfg())
    with pytest.raises(RuntimeError):
        port.submit([EnrichmentRequest(company="vibes")])


def test_clay_retrieve_pending_when_batch_absent():
    cfg = _cfg(clay_webhook_url="https://x", enrichment_sheet_id="sid")
    # Sheet has a row, but for a different batch → this batch still pending.
    port = LiveClayEnrichmentPort(cfg, sheet_reader=lambda: [
        {"batch_id": "other", "company": "vibes", "email": "a@b.com"}])
    assert port.retrieve("clay-123") is None


def test_clay_retrieve_parses_sheet_rows():
    cfg = _cfg(clay_webhook_url="https://x", enrichment_sheet_id="sid")
    port = LiveClayEnrichmentPort(cfg, sheet_reader=lambda: [
        {"batch_id": "clay-1", "company": "vibes", "name": "Sarah",
         "email": "sarah@vibes.com", "verified": "true", "title": "VP Sales",
         "linkedin_url": ""},
        {"batch_id": "other", "company": "acme", "name": "X", "email": "x@a.com"},
    ])
    out = port.retrieve("clay-1")
    assert out and len(out) == 1
    assert out[0].email == "sarah@vibes.com" and out[0].verified is True


def test_select_port_none_without_creds():
    assert select_enrichment_port(_cfg()) is None


def test_select_port_live_when_configured():
    cfg = _cfg(clay_webhook_url="https://x", enrichment_sheet_id="sid")
    assert isinstance(select_enrichment_port(cfg), LiveClayEnrichmentPort)


def test_drain_submitted_applies_all_batches(db_path):
    oid = _seed_opp(db_path, "vibes")
    enqueue_company(db_path, "vibes", "VP Sales", oid)
    port = FakeEnrichmentPort({"vibes": [EnrichmentResult(
        company="vibes", name="Sarah", email="sarah@vibes.com", verified=True)]})
    submit_pending(db_path, port)
    written = drain_submitted(db_path, port)
    assert written == 1
    with cursor(db_path) as cur:
        row = cur.execute("SELECT status FROM enrichment_queue LIMIT 1").fetchone()
    assert row["status"] == "done"


def test_enrichment_jobs_noop_without_creds(db_path, monkeypatch):
    # run_job for enrichment jobs must no-op (not raise) when unprovisioned.
    from banks import jobs
    monkeypatch.setattr("banks.config.load_config", lambda: _cfg())
    assert jobs.run_job("enrichment_submit", db_path, FakeChatPort()) is None
    assert jobs.run_job("enrichment_retrieve", db_path, FakeChatPort()) is None
