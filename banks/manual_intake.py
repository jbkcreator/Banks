"""Manual Intake Surface (MOD-01, plan line 98).

Three ways for Josh to add an opportunity by hand, all feeding the same
`intake` pipeline (dedup -> exclusion -> score -> tier -> record -> surface):

  * paste a full job description  -> comp (regex) + industry (LLM) known,
    so the role is fully scored and CAN reach Tier A/B and surface now.
  * paste a job URL               -> title/company only until a JD arrives,
    held for enrichment like a Simplify row.
  * quick "I applied here"        -> title + company, held for enrichment.

This surface is where the compensation extractor lives — the answer to the
"Simplify has no salary" problem (Decision 4). A pasted JD yields real comp, so
tiering stops being half-blind. The Slack-command entry point is a one-line call
into `ingest_manual()` wired from the MOD-05 listener; the CLI entry point is
`python -m banks.manual_intake` (see __main__).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .chatport import ChatPort
from .dedup import find_duplicate
from .exclusion import is_target_excluded
from .flow import Proposed
from .intake import _surface_opportunity  # reuse the one surfacing builder
from .llmport import LLMPort
from .normalise import classify_pursuit_mode, normalise_company
from .opportunity import mark_application_drafted, record_opportunity
from . import score as _score

# --- Compensation extraction ------------------------------------------------
# Pull an annual BASE figure in thousands ($k) from free-text JD copy. We take
# the LOWER bound of any range (conservative — score against what's guaranteed).

_COMP_FLOOR_K, _COMP_CEIL_K = 80, 900  # plausible annual-base window, in $k

# One money token, captured whole so we never mis-split the digits:
#   $150,000 | 150,000 | $150k | 150K | $95,000 | 180000 | $230,000
_MONEY = re.compile(
    r"""\$?\s*
        (?P<num>\d{1,3}(?:,\d{3})+     # comma-grouped: 150,000 / 95,000
              | \d{2,3}\s*[kK]         # k-suffixed:    150k / 95K
              | \d{4,7}                # bare thousands: 180000
              | \d{2,3})               # bare 2-3 digit: 150
        (?P<k>\s*[kK])?
    """,
    re.VERBOSE,
)
_COMP_CONTEXT = re.compile(r"(base|salary|compensation|pay|OTE|\$)", re.IGNORECASE)


def _to_thousands(raw: str, had_k: bool) -> float:
    """Normalise one captured money token to $k."""
    digits = raw.replace(",", "").strip().rstrip("kK").strip()
    n = float(digits)
    if had_k or "k" in raw.lower():
        return n                     # already in thousands (150k -> 150)
    if "," in raw or n >= 1000:
        return n / 1000              # 150,000 / 180000 -> 150
    return n                         # bare 2-3 digit -> already thousands (150)


def extract_comp_k(text: str) -> float | None:
    """Return the annual base in thousands (e.g. 150.0 for $150k), or None.

    Considers only figures near comp language (skips "500 employees",
    "founded 2019"), normalises each whole token to $k, keeps those in the
    $80k–$900k window, and returns the lower bound of any range.
    """
    candidates: list[float] = []
    for m in _MONEY.finditer(text):
        start = max(0, m.start() - 40)
        if not _COMP_CONTEXT.search(text[start:m.start() + 1]):
            continue
        k = _to_thousands(m.group("num"), bool(m.group("k")))
        if _COMP_FLOOR_K <= k <= _COMP_CEIL_K:
            candidates.append(k)
    return min(candidates) if candidates else None


# --- Manual intake ----------------------------------------------------------

_JD_EXTRACT_SYSTEM = (
    "Extract job details from this posting. Return ONLY JSON with keys: "
    "title (str), company (str), location (str), industry (str). "
    "industry is the company's sector (e.g. PropTech, SaaS, Fintech, HealthTech)."
)


@dataclass(frozen=True)
class ManualIntakeResult:
    opportunity_id: int
    tier: str
    fit: int
    needs_enrichment: bool
    surfaced: bool
    proposal: Proposed | None
    skipped: str | None = None  # "excluded" | "duplicate" | None


def ingest_manual(
    db_path: str,
    chat: ChatPort,
    *,
    jd_text: str | None = None,
    url: str | None = None,
    title: str | None = None,
    company: str | None = None,
    location: str = "",
    llm: LLMPort | None = None,
    surface_tiers: tuple[str, ...] = ("A", "B"),
) -> ManualIntakeResult:
    """Add one opportunity by hand. If `jd_text` is given, extract comp+industry
    so the role is fully scored; otherwise (URL or quick input) hold for
    enrichment. `title`/`company` override or supply what the JD lacks.
    """
    industry: str | None = None
    comp_k: float | None = None

    if jd_text:
        comp_k = extract_comp_k(jd_text)
        if llm is not None:
            ex = llm.extract_json(_JD_EXTRACT_SYSTEM, jd_text[:6000])
            title = title or ex.get("title")
            company = company or ex.get("company")
            location = location or ex.get("location") or ""
            industry = ex.get("industry")

    title = (title or "").strip()
    company = (company or "").strip()
    if not title or not company:
        raise ValueError("manual intake needs at least a title and company")

    if is_target_excluded(db_path, company=company)[0]:
        return ManualIntakeResult(-1, "-", 0, False, False, None, skipped="excluded")

    source_url = (url or "").strip() or None
    dup = find_duplicate(db_path, source_url, title, company)
    if dup is not None:
        return ManualIntakeResult(dup, "-", 0, False, False, None, skipped="duplicate")

    pursuit_mode = classify_pursuit_mode(f"{title} {jd_text or ''}")
    fit, tier, needs_enrichment = _score.score_role(
        comp_k=comp_k, industry=industry, location=location, pursuit_mode=pursuit_mode)

    opp_id = record_opportunity(
        db_path, title, "manual", fit,
        tier=tier, pursuit_mode=pursuit_mode,
        company_normalized=normalise_company(company), source_url=source_url,
        needs_enrichment=needs_enrichment, industry=industry,
    )

    proposal = None
    surfaced = False
    if not needs_enrichment and tier in surface_tiers:
        parsed = {"title": title, "company": company, "location": location,
                  "industry": industry}
        proposal = _surface_opportunity(db_path, chat, opp_id, parsed, fit, tier, pursuit_mode)
        mark_application_drafted(db_path, opp_id)
        surfaced = True

    return ManualIntakeResult(opp_id, tier, fit, needs_enrichment, surfaced, proposal)


# --- CLI entry point --------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    import argparse
    from .chatport import LiveChatPort
    from .config import load_config
    from .llmport import load_llm_port

    p = argparse.ArgumentParser(prog="banks.manual_intake",
                                description="Add a job opportunity by hand.")
    p.add_argument("--jd-file", help="path to a text file with the job description")
    p.add_argument("--url", help="job posting URL")
    p.add_argument("--title")
    p.add_argument("--company")
    p.add_argument("--location", default="")
    args = p.parse_args(argv)

    jd_text = None
    if args.jd_file:
        with open(args.jd_file, encoding="utf-8") as f:
            jd_text = f.read()

    db_path = load_config().db_path
    res = ingest_manual(
        db_path, LiveChatPort(),
        jd_text=jd_text, url=args.url, title=args.title,
        company=args.company, location=args.location,
        llm=load_llm_port(),
    )
    if res.skipped:
        print(f"skipped ({res.skipped}) — opportunity {res.opportunity_id}")
    else:
        state = "surfaced to Slack" if res.surfaced else (
            "held for enrichment" if res.needs_enrichment else "recorded (Tier C)")
        print(f"opportunity {res.opportunity_id}: Tier {res.tier}, fit {res.fit}/100 — {state}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
