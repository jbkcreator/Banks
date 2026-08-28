"""MOD-01 Slack CSV upload — should_ingest_file gate + intake integration."""
from __future__ import annotations

import dataclasses
import os
import tempfile

import pytest

from banks.chatport import FakeChatPort
from banks.config import load_config
from banks.csvport import FakeCSVPort
from banks.intake import ingest_simplify
from banks.socket_listener import should_ingest_file
from banks.store import init_db


def _cfg():
    return dataclasses.replace(
        load_config(), approver_user_id="UJOSH", slack_jobs_channel_id="CJOBS",
    )


def _evt(**kw):
    e = {"user": "UJOSH", "channel": "CJOBS",
         "files": [{"name": "simplify.csv", "filetype": "csv"}]}
    e.update(kw)
    return e


def test_gate_accepts_josh_csv_in_jobs_channel():
    assert should_ingest_file(_cfg(), _evt()) is True


def test_gate_rejects_wrong_channel():
    assert should_ingest_file(_cfg(), _evt(channel="CRANDOM")) is False


def test_gate_rejects_unauthorized_user():
    assert should_ingest_file(_cfg(), _evt(user="USOMEONE")) is False


def test_gate_rejects_bot_file():
    assert should_ingest_file(_cfg(), _evt(bot_id="B1")) is False


def test_gate_rejects_non_csv():
    assert should_ingest_file(
        _cfg(), _evt(files=[{"name": "resume.pdf", "filetype": "pdf"}])
    ) is False


def test_gate_rejects_no_files():
    assert should_ingest_file(_cfg(), _evt(files=[])) is False


# --- intake integration: a Simplify export produces the expected counts -------

@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _simplify_rows():
    return [
        {"Job Title": "VP Sales", "Company Name": "Acme", "Location": "Remote",
         "Job URL": "https://x.com/1", "Applied Date": "2026-08-01", "Status": "Applied"},
        {"Job Title": "AE", "Company Name": "Beta", "Location": "Tampa, FL",
         "Job URL": "https://x.com/2", "Applied Date": "2026-08-02", "Status": "Applied"},
        # duplicate of row 1 (same URL)
        {"Job Title": "VP Sales", "Company Name": "Acme", "Location": "Remote",
         "Job URL": "https://x.com/1", "Applied Date": "2026-08-01", "Status": "Applied"},
    ]


def test_ingest_simplify_counts(db_path):
    res = ingest_simplify(db_path, FakeCSVPort(_simplify_rows()), "ignored", FakeChatPort())
    assert res.ingested == 2
    assert res.duplicates == 1
    # Simplify rows have no industry → held for enrichment, not surfaced
    assert res.held == 2
    assert res.surfaced == 0
