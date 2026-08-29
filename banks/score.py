"""Fit scorer. Weighted comp / vertical / geo / pursuit → 0–100 → tier.

All tunable policy (weights, thresholds, comp band, vertical keyword sets, geo
keywords, preferred modes) lives on ScoringConfig — one place, not literals
scattered through the functions and duplicated in this docstring. Defaults are
Josh's (locked 2026-08-25); a second user gets a different ScoringConfig, not a
code fork. See DEFAULT_SCORING for the current values.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringConfig:
    """Tunable scoring policy — the knobs that are person-specific, as data."""

    tier_a_min: int = 75
    tier_b_min: int = 50  # below this = Tier C
    comp_floor_k: int = 150
    comp_sweet_k: int = 220
    # weights (sum 100)
    weight_comp: int = 35
    weight_vertical: int = 25
    weight_geo: int = 20
    weight_pursuit: int = 20
    full_verticals: frozenset[str] = frozenset(
        {"proptech", "real estate tech", "saas", "fintech", "financial technology"}
    )
    partial_verticals: frozenset[str] = frozenset(
        {"hr tech", "hrtech", "insurtech", "legaltech", "edtech", "healthtech"}
    )
    geo_full: tuple[str, ...] = ("remote", "tampa", "florida", ", fl")
    geo_hybrid: tuple[str, ...] = ("hybrid",)
    preferred_modes: frozenset[str] = frozenset({"full_time", "contract_to_hire"})


DEFAULT_SCORING = ScoringConfig()


def assign_tier(score: int, cfg: ScoringConfig = DEFAULT_SCORING) -> str:
    if score >= cfg.tier_a_min:
        return "A"
    if score >= cfg.tier_b_min:
        return "B"
    return "C"


def score_vertical(vertical: str | None, cfg: ScoringConfig = DEFAULT_SCORING) -> float:
    """Return 0.0–1.0 vertical fit score.

    Empty/unknown → 0.5 (neutral, benefit of the doubt) to match score_comp's
    treatment of unknown comp. A *known* non-fit (e.g. "manufacturing") is still
    0.0 — absence of data and evidence of misfit are different.
    """
    if not vertical or not vertical.strip():
        return 0.5
    v = vertical.lower()
    if any(kw in v for kw in cfg.full_verticals):
        return 1.0
    if any(kw in v for kw in cfg.partial_verticals):
        return 0.5
    return 0.0


def score_geo(location: str, remote: bool = False,
              cfg: ScoringConfig = DEFAULT_SCORING) -> float:
    """Return 0.0–1.0 geo fit score."""
    if remote:
        return 1.0
    loc = location.lower()
    if any(kw in loc for kw in cfg.geo_full):
        return 1.0
    if any(kw in loc for kw in cfg.geo_hybrid):
        return 0.5
    return 0.0


def score_pursuit_mode(mode: str, cfg: ScoringConfig = DEFAULT_SCORING) -> float:
    return 1.0 if mode in cfg.preferred_modes else 0.5


def score_comp(base_k: float | None, cfg: ScoringConfig = DEFAULT_SCORING) -> float:
    """Return 0.0–1.0 comp score. Unknown → neutral 0.5."""
    if base_k is None:
        return 0.5  # unknown comp → neutral
    if base_k < cfg.comp_floor_k:
        return 0.0
    if base_k >= cfg.comp_sweet_k:
        return 1.0
    return (base_k - cfg.comp_floor_k) / (cfg.comp_sweet_k - cfg.comp_floor_k)


def compute_fit_score(
    comp_score: float,      # 0.0–1.0 via score_comp()
    vertical_score: float,  # 0.0–1.0
    geo_score: float,       # 0.0–1.0
    pursuit_score: float,   # 0.0–1.0
    cfg: ScoringConfig = DEFAULT_SCORING,
) -> int:
    """Weighted fit score 0–100 (weights from cfg)."""
    raw = (
        comp_score * cfg.weight_comp
        + vertical_score * cfg.weight_vertical
        + geo_score * cfg.weight_geo
        + pursuit_score * cfg.weight_pursuit
    )
    return round(raw)


# Target-watchlist bump by priority (item 6). Kept here so the boost is part of
# the single fit→tier decision; the caller supplies the priority (the db lookup
# lives in targets.py, keeping score.py pure). See banks/targets.py.
TARGET_BONUS: dict[int, int] = {1: 12, 2: 8, 3: 4}


def score_role(comp_k, industry, location, pursuit_mode,
               cfg: ScoringConfig = DEFAULT_SCORING,
               target_priority: int | None = None):
    """Score one role end to end. The single home for the fit→tier→hold decision
    (was copy-pasted across intake/manual_intake/enrich).

    Returns (fit 0–100, tier A/B/C, needs_enrichment). needs_enrichment gates on
    INDUSTRY — postings rarely publish salary, so requiring comp would hold every
    row forever; comp stays neutral (0.5) when unknown.

    target_priority (1/2/3) adds a graded watchlist bump to the fit, capped at
    100, so a posting at one of Josh's target companies floats up. None = no bump.
    """
    fit = compute_fit_score(
        score_comp(comp_k, cfg),
        score_vertical(industry, cfg),
        score_geo(location or "", cfg=cfg),
        score_pursuit_mode(pursuit_mode, cfg),
        cfg,
    )
    if target_priority is not None:
        fit = min(100, fit + TARGET_BONUS.get(target_priority, 0))
    needs_enrichment = not (industry and str(industry).strip())
    return fit, assign_tier(fit, cfg), needs_enrichment
