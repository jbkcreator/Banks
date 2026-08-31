"""Remote preference (client 2026-08-31): hybrid/onsite outside the home market
sinks to Tier C when remote_only is on; remote + home-market stay."""
from __future__ import annotations

import pytest

from banks.score import REMOTE_RELOCATION_PENALTY, requires_relocation, score_role


@pytest.mark.parametrize("location,expected", [
    ("Remote", False),
    ("Remote (US)", False),
    ("Tampa, FL", False),           # home market — he lives there
    ("Tampa (hybrid)", False),      # home market, still OK
    ("New York (hybrid)", True),    # the exact case Josh flagged
    ("New York", True),
    ("Austin, TX (onsite)", True),
    ("", False),                    # unknown -> don't penalise
])
def test_requires_relocation(location, expected):
    assert requires_relocation(location) is expected


def test_ny_hybrid_target_role_sinks_when_remote_only():
    # EliseAI (target +12) AE (+10) but New York hybrid: Tier A off, Tier C on.
    off = score_role(130, "proptech", "New York (hybrid)", "full_time",
                     target_priority=1, role_type="ae", remote_only=False)
    on = score_role(130, "proptech", "New York (hybrid)", "full_time",
                    target_priority=1, role_type="ae", remote_only=True)
    assert off[1] == "A"
    assert on[1] == "C"
    assert on[0] == max(0, off[0] + REMOTE_RELOCATION_PENALTY)


def test_remote_version_of_same_role_stays():
    on = score_role(130, "proptech", "Remote", "full_time",
                    target_priority=1, role_type="ae", remote_only=True)
    assert on[1] == "A"  # no penalty for a remote role


def test_home_market_role_not_penalised():
    on = score_role(130, "proptech", "Tampa, FL", "full_time",
                    target_priority=1, role_type="ae", remote_only=True)
    off = score_role(130, "proptech", "Tampa, FL", "full_time",
                     target_priority=1, role_type="ae", remote_only=False)
    assert on[0] == off[0]  # untouched


def test_flag_off_is_a_no_op():
    a = score_role(130, "proptech", "New York (hybrid)", "full_time")
    b = score_role(130, "proptech", "New York (hybrid)", "full_time", remote_only=False)
    assert a[0] == b[0]
