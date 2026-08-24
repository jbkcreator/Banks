import pytest

from banks.opportunity import (
    CareerFacts,
    UnknownCareerFact,
    draft_application,
    interview_brief,
    mark_application_drafted,
    record_opportunity,
)
from banks.store import cursor


def test_draft_application_refuses_with_empty_career_facts():
    empty = CareerFacts()
    with pytest.raises(UnknownCareerFact):
        draft_application("Senior Analyst role", empty)


def test_draft_application_only_uses_provided_facts():
    facts = CareerFacts(
        identity="Josh K., Florida-based investor",
        experience=("Managed 12 rental units 2019-present",),
        skills=("Real estate operations",),
    )

    draft = draft_application("Board seat — Property Co", facts)

    assert "Managed 12 rental units" in draft.body
    assert "Real estate operations" in draft.body
    # No embellishment: nothing about education appears since none was given
    assert "Education" not in draft.body


def test_application_never_marked_submitted(db_path):
    opp_id = record_opportunity(db_path, "Board seat", "LinkedIn", match_score=80)
    mark_application_drafted(db_path, opp_id)

    with cursor(db_path) as cur:
        cur.execute("SELECT status, submitted FROM opportunities WHERE id = ?", (opp_id,))
        row = cur.fetchone()

    assert row["status"] == "drafted"
    assert row["submitted"] == 0  # never set to 1 anywhere in the module


def test_interview_brief_requires_career_facts():
    with pytest.raises(UnknownCareerFact):
        interview_brief("Board seat", CareerFacts())

    facts = CareerFacts(experience=("12 years rental ops",), seeking="Board/advisory roles")
    brief = interview_brief("Board seat", facts)
    assert "12 years rental ops" in brief.body
    assert "Board/advisory roles" in brief.body
