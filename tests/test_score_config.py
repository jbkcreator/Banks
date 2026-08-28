"""ScoringConfig makes the scoring policy tunable as data (FIX 5)."""
from __future__ import annotations

import dataclasses

from banks.score import DEFAULT_SCORING, score_comp, score_role


def test_default_comp_band():
    assert score_comp(140) == 0.0          # below floor 150
    assert score_comp(220) == 1.0          # at sweet spot
    assert score_comp(None) == 0.5         # unknown → neutral


def test_custom_config_changes_comp_score():
    # Lower the floor to 100 → a $140k role now scores above zero.
    cfg = dataclasses.replace(DEFAULT_SCORING, comp_floor_k=100, comp_sweet_k=200)
    assert score_comp(140, cfg) > 0.0
    assert score_comp(140) == 0.0          # default unaffected


def test_custom_tier_threshold():
    cfg = dataclasses.replace(DEFAULT_SCORING, tier_a_min=95)
    # $220k PropTech remote full-time = 100 under default → A; still A at 95.
    fit, tier, _ = score_role(220, "PropTech", "Remote", "full_time", cfg)
    assert tier == "A"
    # Raise the bar above 100 → same role drops to B.
    cfg2 = dataclasses.replace(DEFAULT_SCORING, tier_a_min=101)
    _, tier2, _ = score_role(220, "PropTech", "Remote", "full_time", cfg2)
    assert tier2 == "B"
