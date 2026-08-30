"""Tests for all 11 new build items."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from banks.store import init_db


@pytest.fixture()
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


# ── #1 activity_log ──────────────────────────────────────────────────────────

def test_log_event_inserts_row(db):
    from banks.activity_log import log_event
    from banks.store import cursor
    row_id = log_event(db, "draft_created", ref="1", meta={"test": True})
    assert row_id > 0
    with cursor(db) as cur:
        cur.execute("SELECT * FROM activity_log WHERE id = ?", (row_id,))
        row = dict(cur.fetchone())
    assert row["kind"] == "draft_created"
    assert row["minutes_saved"] == 5.0
    assert json.loads(row["meta"]) == {"test": True}


def test_log_event_custom_minutes(db):
    from banks.activity_log import log_event
    log_event(db, "opportunity_drafted", minutes_saved=45.0)
    from banks.store import cursor
    with cursor(db) as cur:
        cur.execute("SELECT minutes_saved FROM activity_log ORDER BY id DESC LIMIT 1")
        assert cur.fetchone()["minutes_saved"] == 45.0


# ── #2 ROI meter ──────────────────────────────────────────────────────────────

def test_hours_saved_this_week_sums_current_week(db):
    from banks.activity_log import hours_saved_this_week, log_event
    log_event(db, "draft_created", minutes_saved=60.0)   # 1h
    log_event(db, "vacancy_flagged", minutes_saved=30.0)  # 0.5h
    hrs = hours_saved_this_week(db)
    assert hrs == pytest.approx(1.5)


def test_hours_saved_excludes_old_events(db):
    from banks.activity_log import hours_saved_this_week
    from banks.store import cursor
    # Insert event with timestamp 2 weeks ago.
    old_ts = "2020-01-01T00:00:00+00:00"
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO activity_log (kind, minutes_saved, ts) VALUES (?, ?, ?)",
            ("draft_created", 999.0, old_ts),
        )
    hrs = hours_saved_this_week(db)
    assert hrs == 0.0


# ── #3 weekly scorecard post ─────────────────────────────────────────────────

def test_weekly_scorecard_job_posts(db):
    from banks.chatport import FakeChatPort
    from banks.jobs import run_job
    chat = FakeChatPort()
    result = run_job("weekly_scorecard", db, chat)
    assert result is not None
    assert len(chat.posts) == 1
    header_text = chat.posts[0]["blocks"][0]["text"]["text"]
    assert "Weekly Scorecard" in header_text


def test_weekly_scorecard_shows_roi(db):
    from banks.activity_log import log_event
    from banks.chatport import FakeChatPort
    from banks.jobs import run_job
    log_event(db, "draft_created", minutes_saved=120.0)
    chat = FakeChatPort()
    run_job("weekly_scorecard", db, chat)
    body = chat.posts[0]["blocks"][1]["text"]["text"]
    assert "2.0h" in body


# ── #4 LLM port ───────────────────────────────────────────────────────────────

def test_fake_llm_returns_registered_response():
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort()
    llm.register("electric bill", '{"name": "Electric", "amount_cents": 5000}')
    result = llm.complete("extract", "electric bill for $50")
    assert "Electric" in result


def test_fake_llm_extract_json():
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort({"invoice": '{"vendor": "Plumber", "amount_cents": 20000}'})
    data = llm.extract_json("extract", "invoice for plumbing work")
    assert data["vendor"] == "Plumber"


def test_claude_llm_port_raises_without_key(monkeypatch):
    # Force a clean env: the live server exports BANKS_ANTHROPIC_API_KEY, and the
    # port falls back to load_config() when api_key is None — delenv so the
    # "no key -> raise" contract is tested regardless of shell state.
    monkeypatch.delenv("BANKS_ANTHROPIC_API_KEY", raising=False)
    from banks.llmport import ClaudeLLMPort
    with pytest.raises(ValueError, match="BANKS_ANTHROPIC_API_KEY"):
        ClaudeLLMPort(api_key=None)


def test_load_llm_port_returns_fake_without_key(monkeypatch):
    from banks.llmport import FakeLLMPort, load_llm_port
    monkeypatch.delenv("BANKS_ANTHROPIC_API_KEY", raising=False)
    port = load_llm_port()
    assert isinstance(port, FakeLLMPort)


# ── #5 bill pipeline ─────────────────────────────────────────────────────────

SAMPLE_EML = """\
From: billing@utility.com
To: josh@example.com
Subject: Your electric bill — $85.00 due 2026-08-15

Your account summary:
Amount due: $85.00
Due date: August 15, 2026
Cadence: monthly
"""


def test_extract_bill_from_email(db):
    from banks.finance import extract_bill_from_email
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort()
    llm.register(
        "electric bill",
        '{"name":"Electric","amount_cents":8500,"due_date":"2026-08-15",'
        '"cadence":"monthly","is_subscription":false,"property_address":null}',
    )
    result = extract_bill_from_email(SAMPLE_EML, llm)
    assert result["name"] == "Electric"
    assert result["amount_cents"] == 8500


def test_upsert_bill_from_extract(db):
    from banks.finance import upsert_bill_from_extract
    from banks.store import cursor
    bill_id = upsert_bill_from_extract(db, {
        "name": "Electric",
        "amount_cents": 8500,
        "due_date": "2026-08-15",
        "cadence": "monthly",
        "is_subscription": False,
        "property_address": None,
    })
    assert bill_id is not None
    with cursor(db) as cur:
        cur.execute("SELECT name FROM bills WHERE id = ?", (bill_id,))
        assert cur.fetchone()["name"] == "Electric"


def test_bill_confirm_draft_shape():
    from banks.finance import bill_confirm_draft
    draft = bill_confirm_draft({
        "name": "Internet", "amount_cents": 6000,
        "due_date": "2026-08-20", "cadence": "monthly",
    })
    assert draft.kind == "bill_confirm"
    assert "Internet" in draft.subject


# ── #6 receipt filing ─────────────────────────────────────────────────────────

RECEIPT_EML = """\
From: receipts@vendor.com
To: josh@example.com
Subject: Receipt — Plumbing repair $250

Vendor: Bob's Plumbing
Amount: $250.00
Date: 2026-08-04
Property: 123 Main St
"""


def test_file_receipt_from_eml_fake(db):
    from banks.fileport import FakeFilePort, file_receipt_from_eml
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort()
    llm.register(
        "plumbing",
        '{"vendor":"Bob Plumbing","amount_cents":25000,'
        '"date":"2026-08-04","property_address":"123 Main St","description":"repair"}',
    )
    file_port = FakeFilePort()
    receipt = file_receipt_from_eml(RECEIPT_EML, llm, file_port)
    assert receipt.vendor == "Bob Plumbing"
    assert len(file_port.uploads) == 1
    assert "2026-08-04" in file_port.uploads[0]["name"]


def test_fake_file_port_upload():
    from banks.fileport import FakeFilePort
    fp = FakeFilePort()
    result = fp.upload("test.pdf", b"pdfdata", "application/pdf")
    assert result.drive_id == "fake_1"
    assert result.name == "test.pdf"


# ── #7 opportunity pipeline ───────────────────────────────────────────────────

POSTING_EML = """\
From: linkedin@linkedin.com
To: josh@example.com
Subject: Director of PropTech at Acme Corp

Acme Corp is hiring a Director of PropTech.
Requirements:
- 5+ years real estate technology leadership
- Python experience
- MBA preferred
Salary: $180k-$220k
"""


def test_process_forwarded_posting(db):
    from banks.llmport import FakeLLMPort
    from banks.opportunity import CareerFacts, process_forwarded_posting
    llm = FakeLLMPort()
    llm.register(
        "director",
        '{"title":"Director of PropTech","company":"Acme Corp",'
        '"key_requirements":["5+ years PropTech","Python"],"salary_range":"180k-220k",'
        '"role_type":"Director","location":"Remote","source":"LinkedIn"}',
    )
    llm.register(
        "requirements",
        '{"gaps":["MBA preferred"],"match_score":78}',
    )
    facts = CareerFacts(
        identity="Josh, proptech operator",
        experience=("5 years real estate ops",),
        skills=("Python", "property management"),
    )
    result = process_forwarded_posting(POSTING_EML, facts, llm, db_path=db)
    assert result.match_score == 78
    assert any("MBA" in g for g in result.gaps)
    assert "Director" in result.draft.subject


# ── #8 classify ──────────────────────────────────────────────────────────────

def test_classify_message_high_confidence():
    from banks.classify import classify_message
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort()
    llm.register(
        "room 3",
        '{"kind":"tenant_inquiry","confidence":0.92,"summary":"Prospect asking about Room 3",'
        '"action_hint":"send_application_link"}',
    )
    result = classify_message("Hi I'm interested in Room 3", llm)
    assert result.kind == "tenant_inquiry"
    assert not result.needs_confirm


def test_classify_message_low_confidence_needs_confirm():
    from banks.classify import classify_message
    from banks.llmport import FakeLLMPort
    llm = FakeLLMPort()
    llm.register(
        "unclear",
        '{"kind":"unknown","confidence":0.4,"summary":"Unclear message",'
        '"action_hint":null}',
    )
    result = classify_message("something unclear happened", llm)
    assert result.needs_confirm


def test_ambiguity_confirm_draft():
    from banks.classify import ClassifyResult, ambiguity_confirm_draft
    cr = ClassifyResult(
        kind="unknown", confidence=0.4, summary="Unclear",
        action_hint=None, needs_confirm=True, raw_text="some message",
    )
    draft = ambiguity_confirm_draft(cr)
    assert draft.kind == "classify_confirm"
    assert "unsure" in draft.subject.lower()


# ── #9 surfacing wired to propose() ──────────────────────────────────────────

def test_surface_review_request(db):
    from banks.chatport import FakeChatPort
    from banks.rentals import surface_review_request
    from banks.store import cursor
    # Seed a room.
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO rooms (property_address, unit_label, rented_by_room, occupied, updated_at) "
            "VALUES ('123 Main', 'Room 1', 1, 1, '2026-01-01')"
        )
    chat = FakeChatPort()
    proposed = surface_review_request(db, "tenant@example.com", "123 Main", chat)
    assert proposed is not None
    assert len(chat.posts) == 1


def test_surface_occasion(db):
    from banks.chatport import FakeChatPort
    from banks.rentals import surface_occasion
    chat = FakeChatPort()
    proposed = surface_occasion(db, "Mom's birthday", "mom@family.com", chat)
    assert proposed is not None


# ── #10 DI container ─────────────────────────────────────────────────────────

def test_container_fake(db):
    from banks.container import Container
    c = Container.fake(db_path=db)
    assert c.db_path == db
    from banks.chatport import FakeChatPort
    from banks.mailer import FakeMailer
    assert isinstance(c.chat, FakeChatPort)
    assert isinstance(c.mailer, FakeMailer)


def test_container_live_raises_without_slack(monkeypatch, db):
    from banks.container import Container
    monkeypatch.delenv("BANKS_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BANKS_CHANNEL_ID", raising=False)
    with pytest.raises(ValueError, match="Slack credentials"):
        Container.live(db_path=db)


# ── #11 nightly reflection ────────────────────────────────────────────────────

def test_reflection_sections_empty(db):
    from banks.reflection import reflection_sections
    sections = reflection_sections(db)
    titles = [s[0] for s in sections]
    assert any("Today" in t for t in titles)


def test_reflection_sections_with_events(db):
    from banks.activity_log import log_event
    from banks.reflection import reflection_sections
    log_event(db, "draft_created")
    log_event(db, "vacancy_flagged")
    sections = reflection_sections(db)
    summary_lines = sections[0][1]
    assert "2 actions" in summary_lines[0]


def test_run_reflection_posts_to_slack(db):
    from banks.chatport import FakeChatPort
    from banks.reflection import run_reflection
    chat = FakeChatPort()
    result = run_reflection(db, chat)
    assert result is not None
    assert len(chat.posts) == 1
    assert "Nightly Recap" in chat.posts[0]["blocks"][0]["text"]["text"]


def test_nightly_reflection_job(db):
    from banks.chatport import FakeChatPort
    from banks.jobs import run_job
    chat = FakeChatPort()
    result = run_job("nightly_reflection", db, chat)
    assert result is not None
