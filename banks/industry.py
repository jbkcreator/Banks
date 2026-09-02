"""Classify a company's industry so held opportunities can be scored (MOD-01).

Every Simplify row lands with `needs_enrichment=1` because the export carries
neither salary nor industry, and the fit scorer weights Vertical at 25 points.
With industry unknown it defaults to a neutral 0.5, so all 43 of Josh's
opportunities sat on half-blind tiers and none could be surfaced (Decision 4).

`enrich.enrich_opportunity()` already solves this the thorough way — fetch the
job posting, extract industry + comp + location from the real text. But it
needs the posting to still be live, and job URLs expire; that path recovers
nothing for a backlog of older applications.

This module takes the cheap, reliable route for the one field that actually
gates surfacing: ask an LLM what industry a *named company* is in. No network
fetch, no scraping, one batched call for the whole backlog. Comp stays unknown
(neutral) — `score_role` gates on industry alone for exactly this reason. Geo
comes from the stored `location` column, added at the same time: it had never
been persisted, so re-scoring without it silently cost 20 points a row.

Deliberately constrained: the model must answer from LABELS, which map onto
score.ScoringConfig's vertical sets. Free text would silently score 0.0 the
moment the model said "real estate services" instead of "real estate tech".

A classification can LOWER a tier, and that is correct: score_vertical returns
0.5 for unknown but 0.0 for a known non-fit. Absence of data and evidence of
misfit are different things, and a private-equity firm scoring below a PropTech
SaaS company is the scorer working, not a regression.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import score as _score
from .store import cursor

# The only answers allowed. First group are full-fit verticals (score 1.0),
# second are partial (0.5), the rest are honest non-fits (0.0). Every label is
# checked against score.ScoringConfig by test_industry.py so a config change
# can never silently strand this vocabulary.
FULL_FIT = ("proptech", "real estate tech", "saas", "fintech")
PARTIAL_FIT = ("hr tech", "insurtech", "legaltech", "edtech", "healthtech")
NON_FIT = (
    "real estate services", "commercial real estate", "private equity",
    "lending", "banking", "insurance carrier", "logistics", "manufacturing",
    "healthcare services", "staffing", "marketing services", "retail",
    "hospitality", "construction", "energy", "other",
)
ALLOWED = FULL_FIT + PARTIAL_FIT + NON_FIT

_SYSTEM = (
    "You label the PRIMARY industry of a company. You will be given a JSON list "
    "of company names.\n"
    "Return JSON only: {\"<company>\": \"<label>\", ...} — one entry per input "
    "company, using the company name EXACTLY as given as the key.\n"
    "The label MUST be one of these, verbatim:\n  "
    + ", ".join(ALLOWED) + "\n"
    "Guidance:\n"
    "- 'proptech' / 'real estate tech': software sold into real estate, rentals, "
    "property management, or construction tech.\n"
    "- 'saas': B2B software that is not primarily real-estate or finance focused.\n"
    "- 'fintech': software for payments, lending, banking, or financial workflows.\n"
    "- Use a NON-FIT label ('private equity', 'commercial real estate', "
    "'lending', 'logistics', …) when the company is NOT primarily a software "
    "vendor — e.g. a PE firm, a brokerage, a direct lender, a manufacturer.\n"
    "- If you genuinely do not recognise the company, use \"other\". Never guess "
    "a flattering label to be helpful — a wrong 'saas' inflates its ranking.\n"
    "- A company that sells SOFTWARE INTO an industry is a software vendor, not "
    "that industry: hotel property-management software is 'saas', not "
    "'hospitality'; construction-maintenance software is 'saas'/'proptech', not "
    "'construction'.\n"
    "IMPORTANT — company names are ambiguous and each item may include "
    "`posting_url`, `role_applied_for` and `location`. The posting_url "
    "identifies WHICH company this actually is; trust it over the bare name. "
    "For example a 'flex' whose posting_url is a small startup's job board is "
    "NOT Flex Ltd the NYSE manufacturer."
)


@dataclass(frozen=True)
class IndustryResult:
    company: str
    industry: str
    opportunities_updated: int
    tiers: tuple[str, ...]


def held_companies(db_path: str) -> list[str]:
    """Companies with at least one opportunity still held for enrichment."""
    with cursor(db_path) as cur:
        return [r["company_normalized"] for r in cur.execute(
            "SELECT DISTINCT company_normalized FROM opportunities "
            "WHERE needs_enrichment = 1 AND company_normalized IS NOT NULL "
            "AND company_normalized != '' ORDER BY company_normalized")]


def company_context(db_path: str, companies: list[str]) -> list[dict]:
    """Disambiguating context per company: a job title and the ATS/posting URL.

    A bare slug is genuinely ambiguous and the model picks the most famous
    match: "flex" classified as `logistics` (Flex Ltd, the NYSE manufacturer)
    when Josh applied to a proptech startup at
    job-boards.greenhouse.io/flex/... — the same name-collision that made the
    Clay domain guesser return Flex Ltd's CEO twice. The posting URL is the one
    unambiguous identifier Banks already stores.
    """
    ctx = []
    with cursor(db_path) as cur:
        for comp in companies:
            row = cur.execute(
                "SELECT title, location, source_url FROM opportunities "
                "WHERE company_normalized = ? AND needs_enrichment = 1 "
                "ORDER BY id LIMIT 1", (comp,)).fetchone()
            ctx.append({
                "company": comp,
                "role_applied_for": (row["title"] if row else "") or "",
                "location": (row["location"] if row else "") or "",
                "posting_url": (row["source_url"] if row else "") or "",
            })
    return ctx


def classify(companies: list[str] | list[dict], llm,
             batch_size: int = 20) -> dict[str, str]:
    """{company: label} for the labels the model returns. Unknown keys dropped.

    Accepts bare names or context dicts from company_context() — pass the
    context where you can, it is what disambiguates a generic company name.

    Batched so a 40-company backlog is a couple of calls, not forty. Anything
    the model returns outside ALLOWED is discarded rather than written — an
    off-vocabulary label would score 0.0 by accident instead of on purpose.
    """
    items: list[dict] = [{"company": c} if isinstance(c, str) else c
                         for c in companies]
    out: dict[str, str] = {}
    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        names = [it["company"] for it in chunk]
        try:
            data = llm.extract_json(_SYSTEM, json.dumps(chunk))
        except Exception as exc:
            print(f"[industry] batch failed: {exc!r}", flush=True)
            continue
        if not isinstance(data, dict):
            continue
        for comp in names:
            label = str(data.get(comp) or "").strip().lower()
            if label in ALLOWED:
                out[comp] = label
            elif label:
                print(f"[industry] discarded off-vocabulary label "
                      f"{label!r} for {comp!r}", flush=True)
    return out


def apply_industry(db_path: str, company: str, industry: str) -> IndustryResult:
    """Write the industry on every held opportunity for `company` and re-score.

    Mirrors enrich.enrich_opportunity's write, minus the posting fetch: comp
    stays None (unknown -> neutral), which is why score_role gates
    needs_enrichment on industry rather than on comp.
    """
    from .config import load_config
    from .normalise import classify_role_type
    from .targets import target_priority

    remote_only = load_config().remote_only_roles
    priority = target_priority(db_path, company)

    with cursor(db_path) as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT id, title, pursuit_mode, location FROM opportunities "
            "WHERE company_normalized = ? AND needs_enrichment = 1", (company,))]

    tiers: list[str] = []
    for row in rows:
        fit, tier, still_held = _score.score_role(
            # Real stored location — passing "" here would drop 20 geo points
            # and quietly demote every row a tier.
            comp_k=None, industry=industry, location=row["location"] or "",
            pursuit_mode=row["pursuit_mode"] or "full_time",
            target_priority=priority,
            role_type=classify_role_type(row["title"] or "", ""),
            remote_only=remote_only)
        with cursor(db_path) as cur:
            cur.execute(
                "UPDATE opportunities SET criteria_match_score = ?, tier = ?, "
                "needs_enrichment = ?, industry = ? WHERE id = ?",
                (fit, tier, 1 if still_held else 0, industry, row["id"]))
        tiers.append(tier)

    return IndustryResult(company, industry, len(rows), tuple(tiers))


def classify_pending(db_path: str, llm) -> list[IndustryResult]:
    """Classify + re-score every held opportunity. Returns per-company results."""
    companies = held_companies(db_path)
    if not companies:
        return []
    labels = classify(company_context(db_path, companies), llm)
    return [apply_industry(db_path, comp, label) for comp, label in labels.items()]
