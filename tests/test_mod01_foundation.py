"""MOD-01 foundation tests: normalise, pursuit mode, dedup, csv parsers."""
import pytest
from banks.normalise import normalise_company, classify_pursuit_mode
from banks.dedup import find_duplicate, _slug
from banks.csvport import (
    FakeCSVPort,
    parse_loopcv_row,
    parse_simplify_row,
    parse_linkedin_connection_row,
    parse_alumni_row,
)
from banks.store.db import init_db


# ---------------------------------------------------------------------------
# normalise_company

@pytest.mark.parametrize("raw,expected", [
    ("Acme Inc.", "acme"),
    ("Widget LLC", "widget"),
    ("Global Corp", "global"),
    ("Startup Ltd.", "startup"),
    ("  TechCo  ", "techco"),
])
def test_normalise_company(raw, expected):
    assert normalise_company(raw) == expected


# ---------------------------------------------------------------------------
# classify_pursuit_mode

def test_classify_full_time():
    assert classify_pursuit_mode("We are hiring a full time VP Sales") == "full_time"

def test_classify_contract():
    assert classify_pursuit_mode("6-month contract role, C2H possible") == "contract_to_hire"

def test_classify_fractional():
    assert classify_pursuit_mode("Looking for a fractional CRO") == "fractional"

def test_classify_consulting():
    assert classify_pursuit_mode("Advisory/consulting engagement, project-based") == "consulting"

def test_classify_default():
    assert classify_pursuit_mode("Senior Director of Growth") == "full_time"


# ---------------------------------------------------------------------------
# find_duplicate

def test_find_duplicate_empty_db(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    assert find_duplicate(db, "https://example.com/job/1", "VP Sales", "Acme") is None


def test_find_duplicate_url_match(tmp_path):
    from banks.store.db import cursor
    db = str(tmp_path / "test.db")
    init_db(db)
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO opportunities (title, source_url, company_normalized, status) "
            "VALUES (?, ?, ?, 'sourced')",
            ("VP Sales", "https://example.com/job/1", "acme"),
        )
        inserted_id = cur.lastrowid
    result = find_duplicate(db, "https://example.com/job/1", "VP Sales", "Acme")
    assert result == inserted_id


def test_find_duplicate_fuzzy_match(tmp_path):
    from banks.store.db import cursor
    db = str(tmp_path / "test.db")
    init_db(db)
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO opportunities (title, company_normalized, status) "
            "VALUES (?, ?, 'sourced')",
            ("VP Sales", "acme"),
        )
        inserted_id = cur.lastrowid
    result = find_duplicate(db, None, "VP Sales", "acme")
    assert result == inserted_id


def test_find_duplicate_no_false_positive(tmp_path):
    from banks.store.db import cursor
    db = str(tmp_path / "test.db")
    init_db(db)
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO opportunities (title, company_normalized, status) "
            "VALUES (?, ?, 'sourced')",
            ("VP Sales", "acme"),
        )
    assert find_duplicate(db, None, "Head of Growth", "acme") is None


# ---------------------------------------------------------------------------
# FakeCSVPort

def test_fake_csv_port_returns_rows():
    rows = [{"a": "1"}, {"a": "2"}]
    port = FakeCSVPort(rows)
    assert port.read_csv("any/path.csv") == rows


# ---------------------------------------------------------------------------
# Row parsers

def test_parse_loopcv_row():
    row = {"Job Title": "VP Sales", "Company": "Acme Inc", "URL": "https://x.com"}
    parsed = parse_loopcv_row(row)
    assert parsed["title"] == "VP Sales"
    assert parsed["source"] == "loopcv"
    assert parsed["source_url"] == "https://x.com"


def test_parse_simplify_row():
    row = {"Role": "CRO", "Company Name": "Widget Co", "Job URL": "https://y.com"}
    parsed = parse_simplify_row(row)
    assert parsed["title"] == "CRO"
    assert parsed["source"] == "simplify"


def test_parse_linkedin_connection_row():
    row = {"First Name": "Jane", "Last Name": "Doe", "Company": "Startup LLC", "Email Address": "jane@startup.com"}
    parsed = parse_linkedin_connection_row(row)
    assert parsed["name"] == "Jane Doe"
    assert parsed["company"] == "startup"
    assert parsed["source"] == "linkedin_csv"
    assert parsed["degree"] == 1


def test_parse_alumni_row():
    row = {"name": "Bob Smith", "company": "Tech Corp", "email": "bob@tech.com"}
    parsed = parse_alumni_row(row)
    assert parsed["name"] == "Bob Smith"
    assert parsed["source"] == "alumni_csv"
