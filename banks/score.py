"""Fit scorer. Weights: Comp&Tier 35% / Vertical-Network 25% / Remote-Geo 20% / Pursuit-Mode 20%.

Comp floor/sweet-spot pending Josh's answer (CLIENT_QUERIES.md Q2) — comp_score
stubbed at 0.5 (neutral) until confirmed. All other dimensions are locked.
"""
from __future__ import annotations

# Tier thresholds (locked 2026-08-25)
TIER_A_MIN = 75
TIER_B_MIN = 50  # below this = Tier C


def assign_tier(score: int) -> str:
    if score >= TIER_A_MIN:
        return "A"
    if score >= TIER_B_MIN:
        return "B"
    return "C"


# Vertical fit — full/partial/zero (locked 2026-08-25)
_FULL_VERTICALS = {"proptech", "real estate tech", "saas", "fintech", "financial technology"}
_PARTIAL_VERTICALS = {"hr tech", "hrtech", "insurtech", "legaltech", "edtech", "healthtech"}


def score_vertical(vertical: str) -> float:
    """Return 0.0–1.0 vertical fit score."""
    v = vertical.lower()
    if any(kw in v for kw in _FULL_VERTICALS):
        return 1.0
    if any(kw in v for kw in _PARTIAL_VERTICALS):
        return 0.5
    return 0.0


# Geo scoring (locked 2026-08-25)
def score_geo(location: str, remote: bool = False) -> float:
    """Return 0.0–1.0 geo fit score."""
    if remote:
        return 1.0
    loc = location.lower()
    if "remote" in loc:
        return 1.0
    if "tampa" in loc or "florida" in loc or ", fl" in loc:
        return 1.0
    if "hybrid" in loc:
        return 0.5
    return 0.0


# Pursuit mode alignment (locked 2026-08-25)
_PREFERRED_MODES = {"full_time", "contract_to_hire"}


def score_pursuit_mode(mode: str) -> float:
    return 1.0 if mode in _PREFERRED_MODES else 0.5


def compute_fit_score(
    comp_score: float,      # 0.0–1.0 — stubbed at 0.5 until Q2 answered
    vertical_score: float,  # 0.0–1.0
    geo_score: float,       # 0.0–1.0
    pursuit_score: float,   # 0.0–1.0
) -> int:
    """Weighted fit score 0–100."""
    raw = (
        comp_score * 35
        + vertical_score * 25
        + geo_score * 20
        + pursuit_score * 20
    )
    return round(raw)
