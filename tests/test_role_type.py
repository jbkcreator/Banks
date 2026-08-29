"""Role-type screening (item 7) — target roles lift, SDR/BDR/CS sink."""
from __future__ import annotations

import pytest

from banks.normalise import classify_role_type
from banks.score import ROLE_ADJUST, assign_tier, score_role


@pytest.mark.parametrize("title,expected", [
    ("Account Executive, Mid-Market", "ae"),
    ("Enterprise Sales Executive", "ae"),
    ("Director of Strategic Growth", "strategic_growth"),
    ("Head of Revenue", "strategic_growth"),
    ("Head of Strategic Partnerships", "partnerships"),
    ("VP of Partnerships", "partnerships"),
    ("Sales Development Representative", "sdr_bdr"),
    ("BDR, Outbound", "sdr_bdr"),
    ("Customer Success Manager", "customer_success"),
    ("Account Manager, Renewals", "customer_success"),
    ("Software Engineer", "unknown"),
])
def test_classify_role_type(title, expected):
    assert classify_role_type(title) == expected


def test_anti_type_beats_overlapping_good_keyword():
    # "Sales Development Representative" contains "sales" but must not read as AE.
    assert classify_role_type("Sales Development Representative") == "sdr_bdr"
    # "Account Manager" contains "account" but is CS, not AE.
    assert classify_role_type("Account Manager") == "customer_success"


def test_good_type_lifts_score():
    fit, _t, _ = score_role(None, "proptech", "remote", "full_time", role_type="ae")
    base, _t2, _ = score_role(None, "proptech", "remote", "full_time")
    assert fit == base + 10


def test_anti_type_sinks_best_case_to_tier_c():
    # Best-case anti-type (proptech + remote + neutral comp) must land Tier C.
    fit, tier, _ = score_role(None, "proptech", "remote", "full_time",
                              role_type="sdr_bdr")
    assert tier == "C"
    assert fit < 50


def test_unknown_role_is_neutral():
    with_unknown, _t, _ = score_role(None, "proptech", "remote", "full_time",
                                     role_type="unknown")
    plain, _t2, _ = score_role(None, "proptech", "remote", "full_time")
    assert with_unknown == plain


def test_adjust_clamped_to_floor():
    # A low base plus a big penalty never goes negative.
    fit, _t, _ = score_role(100, "manufacturing", "onsite nowhere", "consulting",
                            role_type="sdr_bdr")
    assert fit >= 0


def test_role_and_target_stack():
    # Company bump + role lift both apply.
    fit, _t, _ = score_role(None, "insurtech", "remote", "full_time",
                            target_priority=1, role_type="ae")
    base, _t2, _ = score_role(None, "insurtech", "remote", "full_time")
    assert fit == min(100, base + 12 + 10)
