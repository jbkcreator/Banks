"""MOD-06 Q5 — deterministic all-Fakes end-to-end mock run.

Opportunity -> surround pack (FakeChat) -> approve a lane -> relay (FakeMailer)
sends. A planted excluded company and excluded person are proven to never
surface a lane and never send. Zero network.
"""
from __future__ import annotations

import datetime as dt

import pytest

from banks.approval import ButtonAction, apply_action
from banks.chatport import FakeChatPort
from banks.exclusion import add_company_exclusion, add_person_exclusion
from banks.mailer import FakeMailer
from banks.opportunity import CareerFacts, record_opportunity
from banks.relay import relay_run
from banks.store import cursor, init_db
from banks.surround import generate_surround_pack


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    return path


FACTS = CareerFacts(
    identity="GTM leader, 15 yrs",
    experience=("VP Sales at PropTech Co",),
    skills=("enterprise sales", "GTM"),
    seeking="VP Sales / CRO",
)


def _verified_contact(db, name, company, linkedin):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, email, linkedin_url, degree, source, "
            "verified, added_at) VALUES (?,?,?,?,1,'linkedin_csv',1,?)",
            (name, company, f"{name.split()[0].lower()}@{company}.com", linkedin,
             dt.datetime.now(dt.timezone.utc).isoformat()))
        return cur.lastrowid


def test_full_pipeline_sends_clean_and_blocks_excluded(db):
    # planted exclusions
    add_company_exclusion(db, "Rent Solutions")
    add_person_exclusion(db, linkedin_url="https://linkedin.com/in/blocked")

    # --- clean Tier A opportunity with a verified hiring-manager contact ------
    cid = _verified_contact(db, "Camryn Hare", "secondnature",
                            "https://linkedin.com/in/camryn")
    clean = record_opportunity(
        db, "Head of Onboarding", "simplify", 88, tier="A",
        company_normalized="secondnature", industry="PropTech", contact_id=cid)

    pack = generate_surround_pack(db, clean, FACTS, FakeChatPort())
    lane_types = {l["type"] for l in pack.lanes}
    assert "hiring_manager" in lane_types      # verified email → outbound lane
    assert pack.blocked == []

    # approve the hiring-manager lane → relay sends exactly it
    hm = next(l for l in pack.lanes if l["type"] == "hiring_manager")
    apply_action(db, ButtonAction.APPROVE, hm["draft_ref"], "U1")
    res = relay_run(db, FakeMailer())
    assert hm["draft_ref"] in res.sent
    assert res.blocked == []

    # --- planted excluded COMPANY never produces a pack ----------------------
    bad_co = record_opportunity(
        db, "AE", "simplify", 80, tier="A", company_normalized="rent solutions")
    bad_pack = generate_surround_pack(db, bad_co, FACTS, FakeChatPort())
    assert bad_pack.lanes == []

    # --- planted excluded PERSON is filtered from an otherwise clean pack -----
    bad_person = _verified_contact(db, "Blocked Person", "thirdco",
                                   "https://linkedin.com/in/blocked")
    opp3 = record_opportunity(
        db, "VP", "simplify", 82, tier="A", company_normalized="thirdco",
        industry="SaaS", contact_id=bad_person)
    pack3 = generate_surround_pack(db, opp3, FACTS, FakeChatPort())
    # the excluded person yields no hiring_manager/linkedin lane; recorded blocked
    assert "Blocked Person" in pack3.blocked
    assert all(l["type"] not in ("hiring_manager", "linkedin") for l in pack3.lanes)
