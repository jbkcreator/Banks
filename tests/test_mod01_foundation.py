"""MOD-01 foundation tests: normalise, pursuit mode, dedup, csv parsers, scoring, cadence."""
import pytest
from banks.normalise import normalise_company, classify_pursuit_mode, map_simplify_status
from banks.dedup import find_duplicate, find_duplicate_contact
from banks.csvport import (
    FakeCSVPort,
    LiveCSVPort,
    parse_loopcv_row,
    parse_simplify_row,
    parse_linkedin_connection_row,
    parse_recruiter_row,
    parse_alumni_row,
)
from banks.score import assign_tier, compute_fit_score, score_geo, score_vertical
from banks.cadence import next_follow_up_date, cadence_complete
from banks.emailport import is_confirmation_email, extract_company_from_subject
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
# map_simplify_status

@pytest.mark.parametrize("raw,expected", [
    ("APPLIED", "applied"),
    ("INTERVIEWING", "interviewing"),
    ("OFFER", "interviewing"),
    ("REJECTED", "closed"),
    ("WITHDRAWN", "closed"),
    ("ARCHIVED", "closed"),
    ("UNKNOWN_STATUS", "sourced"),
])
def test_map_simplify_status(raw, expected):
    assert map_simplify_status(raw) == expected


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
# find_duplicate_contact

def test_find_duplicate_contact_empty(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    assert find_duplicate_contact(db, "https://linkedin.com/in/janedoe") is None


def test_find_duplicate_contact_url_match(tmp_path):
    from banks.store.db import cursor
    import datetime
    db = str(tmp_path / "test.db")
    init_db(db)
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO contacts (name, linkedin_url, degree, source, added_at) "
            "VALUES (?, ?, 1, 'linkedin_csv', ?)",
            ("Jane Doe", "https://linkedin.com/in/janedoe", datetime.datetime.utcnow().isoformat()),
        )
        inserted_id = cur.lastrowid
    assert find_duplicate_contact(db, "https://linkedin.com/in/janedoe") == inserted_id


def test_find_duplicate_contact_empty_url(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    assert find_duplicate_contact(db, "") is None


# ---------------------------------------------------------------------------
# FakeCSVPort / LiveCSVPort

def test_fake_csv_port_returns_rows():
    rows = [{"a": "1"}, {"a": "2"}]
    port = FakeCSVPort(rows)
    assert port.read_csv("any/path.csv") == rows


def test_live_csv_port_skip_until_header(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(
        "Notes: some preamble\n\nFirst Name,Last Name,Company\nJane,Doe,Acme\n",
        encoding="utf-8",
    )
    port = LiveCSVPort()
    rows = port.read_csv(str(csv_file), skip_until_header="First Name")
    assert len(rows) == 1
    assert rows[0]["First Name"] == "Jane"


# ---------------------------------------------------------------------------
# Row parsers

def test_parse_loopcv_row():
    row = {"Job Title": "VP Sales", "Company": "Acme Inc", "URL": "https://x.com"}
    parsed = parse_loopcv_row(row)
    assert parsed["title"] == "VP Sales"
    assert parsed["source"] == "loopcv"
    assert parsed["source_url"] == "https://x.com"


def test_parse_simplify_row_confirmed_columns():
    row = {
        "Job Title": "Account Executive",
        "Company Name": "ButterflyMX",
        "Location": "Remote in USA",
        "Job URL": "https://jobs.ashbyhq.com/butterflymx/123",
        "Applied Date": "2026-08-24",
        "Status": "APPLIED",
        "job_type": "Full-time",
    }
    parsed = parse_simplify_row(row)
    assert parsed["title"] == "Account Executive"
    assert parsed["company"] == "ButterflyMX"
    assert parsed["location"] == "Remote in USA"
    assert parsed["source_url"] == "https://jobs.ashbyhq.com/butterflymx/123"
    assert parsed["status"] == "APPLIED"
    assert parsed["job_type"] == "Full-time"
    assert parsed["source"] == "simplify"


def test_parse_linkedin_connection_row_confirmed_columns():
    row = {
        "First Name": "Jane",
        "Last Name": "Doe",
        "URL": "https://www.linkedin.com/in/janedoe",
        "Email Address": "jane@acme.com",
        "Company": "Acme LLC",
        "Position": "VP Sales",
        "Connected On": "18 Aug 2026",
    }
    parsed = parse_linkedin_connection_row(row)
    assert parsed["name"] == "Jane Doe"
    assert parsed["company"] == "acme"
    assert parsed["linkedin_url"] == "https://www.linkedin.com/in/janedoe"
    assert parsed["position"] == "VP Sales"
    assert parsed["source"] == "linkedin_csv"
    assert parsed["degree"] == 1


def test_parse_recruiter_row_maps_notes():
    row = {
        "First Name": "Tabitha",
        "Last Name": "Francis",
        "Title": "Global Director",
        "Company": "LMRE",
        "Vertical Fit": "PropTech/Real Estate Tech recruiting",
        "LinkedIn URL": "https://www.linkedin.com/in/tabithafrancis/",
        "Notes": "Active relationship - intro call Wed 8/26 8:30am ET",
    }
    parsed = parse_recruiter_row(row)
    assert parsed["name"] == "Tabitha Francis"
    assert parsed["notes"] == "Active relationship - intro call Wed 8/26 8:30am ET"
    assert parsed["vertical_fit"] == "PropTech/Real Estate Tech recruiting"
    assert parsed["source"] == "recruiter_registry"


def test_parse_alumni_row_confirmed_columns():
    row = {
        "First Name": "Aaron",
        "Last Name": "Metaj",
        "Position": "EVP of Sales",
        "Company": "Lima One Capital",
        "LinkedIn URL": "https://www.linkedin.com/in/aaron-metaj-ba3b8414",
        "Connected On": "06 Apr 2026",
    }
    parsed = parse_alumni_row(row)
    assert parsed["name"] == "Aaron Metaj"
    assert parsed["company"] == "lima one capital"
    assert parsed["position"] == "EVP of Sales"
    assert parsed["source"] == "alumni_csv"


# ---------------------------------------------------------------------------
# Tier scoring

def test_assign_tier_a():
    assert assign_tier(80) == "A"

def test_assign_tier_b():
    assert assign_tier(60) == "B"

def test_assign_tier_c():
    assert assign_tier(40) == "C"

def test_assign_tier_boundary_a():
    assert assign_tier(75) == "A"

def test_assign_tier_boundary_b():
    assert assign_tier(50) == "B"


def test_compute_fit_score_all_ones():
    assert compute_fit_score(1.0, 1.0, 1.0, 1.0) == 100

def test_compute_fit_score_all_zeros():
    assert compute_fit_score(0.0, 0.0, 0.0, 0.0) == 0

def test_compute_fit_score_weighted():
    # comp=1.0(35) + vertical=1.0(25) + geo=0.5(10) + pursuit=1.0(20) = 90
    assert compute_fit_score(1.0, 1.0, 0.5, 1.0) == 90


def test_score_geo_remote():
    assert score_geo("Remote in USA", remote=False) == 1.0

def test_score_geo_remote_flag():
    assert score_geo("New York, NY", remote=True) == 1.0

def test_score_geo_tampa():
    assert score_geo("Tampa, FL, USA") == 1.0

def test_score_geo_hybrid():
    assert score_geo("Hybrid - NYC") == 0.5

def test_score_geo_relocation():
    assert score_geo("San Francisco, CA") == 0.0


def test_score_vertical_proptech():
    assert score_vertical("PropTech") == 1.0

def test_score_vertical_saas():
    assert score_vertical("B2B SaaS") == 1.0

def test_score_vertical_hrtech():
    assert score_vertical("HR Tech platform") == 0.5

def test_score_vertical_unrelated():
    assert score_vertical("Manufacturing") == 0.0


# ---------------------------------------------------------------------------
# Follow-up cadence

def test_next_follow_up_day5():
    result = next_follow_up_date("2026-08-24", touches_sent=0)
    assert result == "2026-08-29"

def test_next_follow_up_day21():
    result = next_follow_up_date("2026-08-24", touches_sent=2)
    assert result == "2026-09-14"

def test_next_follow_up_complete():
    assert next_follow_up_date("2026-08-24", touches_sent=3) is None

def test_cadence_complete_max_touches():
    assert cadence_complete(3, "applied") is True

def test_cadence_complete_interviewing():
    assert cadence_complete(1, "interviewing") is True

def test_cadence_not_complete():
    assert cadence_complete(1, "applied") is False


# ---------------------------------------------------------------------------
# Email parser

def test_is_confirmation_email_subject():
    assert is_confirmation_email("Your application to Acme for VP Sales") is True

def test_is_confirmation_email_body():
    assert is_confirmation_email("No match", "Thank you for applying to our team") is True

def test_is_confirmation_email_negative():
    assert is_confirmation_email("Meeting invite", "Let's connect") is False

def test_extract_company_basic():
    result = extract_company_from_subject("Your application to Second Nature for Head of Onboarding")
    assert "Second Nature" in result
