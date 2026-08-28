"""MOD-06: flow.propose enforces the exclusion wall (the every-draft chokepoint).

propose already gates the suppression wall; after the FIX-1 consolidation it also
gates exclusion via the single is_target_excluded predicate when a caller passes
the draft's company/contact.
"""
from __future__ import annotations

import pytest

from banks.chatport import FakeChatPort
from banks.enforcement import Draft
from banks.exclusion import DraftExcluded, add_company_exclusion, add_person_exclusion
from banks.flow import propose
from banks.packets import DecisionPacket
from banks.refs import SendChannel
from banks.store import cursor, init_db


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


def _draft():
    return Draft(kind="hiring_manager", to="hm@acme.com",
                 subject="Interest", body="Hello.")


def _packet():
    return DecisionPacket(kind="hiring_manager", decision="Send outreach?",
                          recommendation="Review and approve",
                          default_if_unanswered="skip")


def test_propose_blocks_excluded_company(db):
    add_company_exclusion(db, "Rent Solutions")
    with pytest.raises(DraftExcluded):
        propose(db, _packet(), _draft(), FakeChatPort(),
                send_channel=SendChannel.INTERNAL, company="Rent Solutions, LLC")


def test_propose_blocks_excluded_person(db):
    add_person_exclusion(db, name="Jane Doe")
    with pytest.raises(DraftExcluded):
        propose(db, _packet(), _draft(), FakeChatPort(),
                send_channel=SendChannel.INTERNAL,
                contact={"name": "Jane Doe", "company": "beta"})


def test_propose_allows_clean_target(db):
    p = propose(db, _packet(), _draft(), FakeChatPort(),
                send_channel=SendChannel.INTERNAL, company="Acme")
    assert p.packet_id > 0


def test_propose_no_target_info_still_works(db):
    # Back-compat: callers that don't pass company/contact are unaffected.
    p = propose(db, _packet(), _draft(), FakeChatPort(),
                send_channel=SendChannel.INTERNAL)
    assert p.packet_id > 0
