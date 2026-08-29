"""Target watchlist (item 6) — graded fit-score boost for Josh's priority companies."""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.score import score_role
from banks.store import init_db
from banks.targets import (add_target, load_targets_from_file, target_bonus,
                           target_priority)


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def test_add_and_lookup_priority(db_path):
    add_target(db_path, "EliseAI", priority=1)
    assert target_priority(db_path, "EliseAI") == 1
    assert target_bonus(db_path, "EliseAI") == 12


def test_matches_despite_casing_and_suffix(db_path):
    add_target(db_path, "EliseAI", priority=1)
    # normalise_company collapses casing / legal suffix
    assert target_priority(db_path, "elise ai, inc.") == 1


def test_unlisted_company_no_bonus(db_path):
    assert target_priority(db_path, "Random Startup") is None
    assert target_bonus(db_path, "Random Startup") == 0


def test_graded_bonus_by_priority(db_path):
    add_target(db_path, "Kiavi", priority=2)
    add_target(db_path, "ClickPay", priority=3)
    assert target_bonus(db_path, "Kiavi") == 8
    assert target_bonus(db_path, "ClickPay") == 4


def test_add_target_idempotent_updates_priority(db_path):
    add_target(db_path, "Obie", priority=2)
    add_target(db_path, "Obie", priority=1)  # re-add upgrades
    assert target_priority(db_path, "Obie") == 1


def test_score_role_applies_bonus_capped_at_100(db_path):
    # A role scoring high already: bonus must not push past 100.
    fit, _tier, _ = score_role(comp_k=300, industry="proptech", location="remote",
                               pursuit_mode="full_time", target_priority=1)
    assert fit == 100


def test_bonus_can_lift_tier(db_path):
    # A partial-vertical role that lands Tier B unaided crosses to A with a bump.
    base_fit, base_tier, _ = score_role(comp_k=None, industry="insurtech",
                                        location="remote", pursuit_mode="full_time")
    up_fit, up_tier, _ = score_role(comp_k=None, industry="insurtech",
                                    location="remote", pursuit_mode="full_time",
                                    target_priority=1)
    assert up_fit == base_fit + 12
    assert up_tier != "C"  # the bump raised it


def test_load_from_file_parses_priorities(tmp_path):
    p = tmp_path / "targets.txt"
    p.write_text(
        "# comment\n"
        "priority 1: EliseAI\n"
        "priority 2: Kiavi\n"
        "priority 3: ClickPay\n"
        "BareName\n",  # no prefix -> defaults to priority 2
        encoding="utf-8",
    )
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(db)
    n = load_targets_from_file(db, str(p))
    assert n == 4
    assert target_priority(db, "EliseAI") == 1
    assert target_priority(db, "ClickPay") == 3
    assert target_priority(db, "BareName") == 2


def test_load_missing_file_is_noop(db_path):
    assert load_targets_from_file(db_path, "does/not/exist.txt") == 0
