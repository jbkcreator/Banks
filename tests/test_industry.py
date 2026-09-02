"""Industry classification — the field that gates surfacing.

All 43 of Josh's opportunities sat held with needs_enrichment=1 because
Simplify carries no industry and the scorer weights Vertical at 25 points.
enrich.enrich_opportunity() solves this properly by fetching the posting, but
job URLs expire, so a backlog recovers nothing. This path classifies from the
company name instead.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from banks.industry import (ALLOWED, FULL_FIT, NON_FIT, PARTIAL_FIT,
                            apply_industry, classify, classify_pending,
                            held_companies)
from banks.score import DEFAULT_SCORING, score_vertical
from banks.store import init_db


class ScriptedLLM:
    def __init__(self, mapping, raise_on_call=False):
        self.mapping = mapping
        self.raise_on_call = raise_on_call
        self.calls = 0

    def extract_json(self, system, user, schema_hint=""):
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("llm down")
        asked = json.loads(user)
        # classify() sends context dicts (or bare names) — handle both.
        names = [a["company"] if isinstance(a, dict) else a for a in asked]
        return {c: self.mapping[c] for c in names if c in self.mapping}


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "i.db")
    init_db(p)
    with sqlite3.connect(p) as c:
        for comp in ("butterflymx", "jones lang lasalle"):
            c.execute("INSERT INTO opportunities (title, company_normalized, source, "
                      "status, tier, needs_enrichment, pursuit_mode, location) "
                      "VALUES (?,?,?,?,?,1,'full_time','Remote in USA')",
                      ("Enterprise Account Executive", comp, "simplify", "applied", "B"))
    return p


# --- the vocabulary must stay in sync with the scorer --------------------

def test_every_full_fit_label_actually_scores_full():
    """A label that doesn't match ScoringConfig scores 0.0 by accident instead
    of on purpose — this is the test that catches a config drift."""
    for label in FULL_FIT:
        assert score_vertical(label) == 1.0, label


def test_every_partial_fit_label_actually_scores_partial():
    for label in PARTIAL_FIT:
        assert score_vertical(label) == 0.5, label


def test_every_non_fit_label_actually_scores_zero():
    """Critical: a non-fit label must not accidentally contain a full-fit
    keyword — e.g. 'real estate services' must not match 'real estate tech'."""
    for label in NON_FIT:
        assert score_vertical(label) == 0.0, label


def test_unknown_is_neutral_not_zero():
    """Absence of data (0.5) and evidence of misfit (0.0) are different."""
    assert score_vertical(None) == 0.5
    assert score_vertical("") == 0.5


# --- classification -----------------------------------------------------

def test_classify_returns_allowed_labels(db):
    llm = ScriptedLLM({"butterflymx": "proptech"})
    assert classify(["butterflymx"], llm) == {"butterflymx": "proptech"}


def test_off_vocabulary_label_is_discarded_not_written(db):
    """A free-text label would score 0.0 silently — drop it instead."""
    llm = ScriptedLLM({"butterflymx": "smart building software"})
    assert classify(["butterflymx"], llm) == {}


def test_llm_failure_is_survivable(db):
    llm = ScriptedLLM({}, raise_on_call=True)
    assert classify(["butterflymx"], llm) == {}


def test_batching_splits_large_backlogs(db):
    names = [f"co{i}" for i in range(45)]
    llm = ScriptedLLM({n: "saas" for n in names})
    out = classify(names, llm, batch_size=20)
    assert len(out) == 45
    assert llm.calls == 3


# --- applying it --------------------------------------------------------

def test_apply_industry_unholds_and_rescores(db):
    res = apply_industry(db, "butterflymx", "proptech")
    assert res.opportunities_updated == 1
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT industry, needs_enrichment, tier FROM opportunities "
                        "WHERE company_normalized='butterflymx'").fetchone()
    assert row[0] == "proptech"
    assert row[1] == 0          # no longer held
    assert row[2] == "A"        # proptech + remote lifts it out of half-blind B


def test_stored_location_is_used_not_dropped(db):
    """Regression: apply_industry passed location="" and silently lost the 20
    geo points, demoting rows a tier. Asserted as a property (with-location
    scores strictly higher) rather than a magic number, so role-type and
    watchlist adjustments can change without making this test lie."""
    apply_industry(db, "butterflymx", "proptech")
    with sqlite3.connect(db) as c:
        with_loc = c.execute("SELECT criteria_match_score FROM opportunities "
                             "WHERE company_normalized='butterflymx'").fetchone()[0]
        # same row, location wiped -> re-hold it and re-score
        c.execute("UPDATE opportunities SET needs_enrichment=1, location=NULL "
                  "WHERE company_normalized='butterflymx'")
    apply_industry(db, "butterflymx", "proptech")
    with sqlite3.connect(db) as c:
        without_loc = c.execute("SELECT criteria_match_score FROM opportunities "
                                "WHERE company_normalized='butterflymx'").fetchone()[0]
    assert with_loc > without_loc, (
        f"stored location must add geo points: {with_loc} vs {without_loc}")
    assert with_loc - without_loc >= DEFAULT_SCORING.weight_geo - 2


def test_known_nonfit_scores_below_a_full_fit(db):
    """This is the scorer working, not a regression: a commercial real-estate
    brokerage is a genuinely worse fit than a PropTech SaaS vendor. Compared
    against a full-fit peer rather than pinned to a tier letter, so tier
    thresholds can move without this test becoming a lie."""
    apply_industry(db, "butterflymx", "proptech")
    apply_industry(db, "jones lang lasalle", "commercial real estate")
    with sqlite3.connect(db) as c:
        fit_fit = c.execute("SELECT criteria_match_score FROM opportunities "
                            "WHERE company_normalized='butterflymx'").fetchone()[0]
        fit_non = c.execute("SELECT criteria_match_score FROM opportunities "
                            "WHERE company_normalized='jones lang lasalle'").fetchone()[0]
    assert fit_non < fit_fit
    # ~weight_vertical apart; not exact because the score rounds to an int.
    assert fit_fit - fit_non >= DEFAULT_SCORING.weight_vertical - 2


def test_held_companies_lists_only_held(db):
    assert set(held_companies(db)) == {"butterflymx", "jones lang lasalle"}
    apply_industry(db, "butterflymx", "proptech")
    assert held_companies(db) == ["jones lang lasalle"]


def test_classify_pending_end_to_end(db):
    llm = ScriptedLLM({"butterflymx": "proptech",
                       "jones lang lasalle": "commercial real estate"})
    results = classify_pending(db, llm)
    assert len(results) == 2
    with sqlite3.connect(db) as c:
        held = c.execute("SELECT COUNT(*) FROM opportunities "
                         "WHERE needs_enrichment=1").fetchone()[0]
    assert held == 0


def test_context_carries_the_posting_url_for_disambiguation(db):
    """A bare slug made the model pick the most famous match: "flex" came back
    `logistics` (Flex Ltd, NYSE) when Josh applied to a proptech startup at
    job-boards.greenhouse.io/flex/... — the same collision that made the Clay
    domain guesser return Flex Ltd's CEO twice."""
    from banks.industry import company_context
    with sqlite3.connect(db) as c:
        c.execute("UPDATE opportunities SET source_url=? "
                  "WHERE company_normalized='butterflymx'",
                  ("https://jobs.ashbyhq.com/butterflymx/abc",))
    ctx = company_context(db, ["butterflymx"])
    assert ctx[0]["posting_url"].startswith("https://jobs.ashbyhq.com/butterflymx")
    assert ctx[0]["role_applied_for"]
    assert ctx[0]["location"] == "Remote in USA"


def test_classify_pending_is_idempotent(db):
    llm = ScriptedLLM({"butterflymx": "proptech",
                       "jones lang lasalle": "commercial real estate"})
    classify_pending(db, llm)
    assert classify_pending(db, llm) == []   # nothing left held
