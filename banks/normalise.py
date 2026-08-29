"""Company normalisation + pursuit-mode classification for MOD-01."""
from __future__ import annotations

import re

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|llc\.?|ltd\.?|corp\.?|co\.?|plc\.?|gmbh|s\.a\.?)\s*$",
    re.IGNORECASE,
)

PURSUIT_MODES = ("full_time", "contract_to_hire", "fractional", "consulting")


def normalise_company(name: str) -> str:
    """Lowercase, collapse internal whitespace, strip trailing legal suffixes.

    Whitespace collapse closes an exclusion-evasion gap: "rent  solutions" (double
    space) must resolve to the same slug as "rent solutions".
    """
    name = re.sub(r"\s+", " ", name.strip().lower())
    name = _LEGAL_SUFFIXES.sub("", name).strip().rstrip(",").strip()
    return name


def normalise_name(name: str | None) -> str | None:
    """Lowercase + collapse internal whitespace — stable key for person exclusion.

    "Jane  Doe", "jane doe", " Jane Doe " all resolve to "jane doe".
    """
    if not name:
        return None
    slug = re.sub(r"\s+", " ", name.strip().lower())
    return slug or None


def classify_pursuit_mode(posting_text: str) -> str:
    """Keyword heuristic — returns one of PURSUIT_MODES.

    Stub until LLMPort call is wired in. Real classification should route
    through LLMPort so Fake works in tests without network.
    """
    text = posting_text.lower()
    if any(w in text for w in ("contract", "c2h", "contract-to-hire")):
        return "contract_to_hire"
    if any(w in text for w in ("fractional", "part-time cro", "part-time cmo")):
        return "fractional"
    if any(w in text for w in ("consulting", "project-based", "advisory")):
        return "consulting"
    return "full_time"


# ---------------------------------------------------------------------------
# Role-type classifier (item 7, 2026-08-29). Screens postings by the three role
# types Josh targets and flags the two he doesn't. Keyword-based, mirroring
# classify_pursuit_mode — deterministic, offline, testable. Feeds a graded score
# adjustment (score.ROLE_ADJUST): the three good types boost, the anti-types
# heavily penalise so a clear SDR/CS posting sinks to Tier C.

# Anti-types checked FIRST — a "Sales Development Representative" contains "sales"
# but must not read as a closing AE role.
def classify_role_type(title: str, jd_text: str = "") -> str:
    """Return one of: ae | strategic_growth | partnerships | sdr_bdr |
    customer_success | unknown. Title dominates; jd_text is a fallback signal."""
    text = f"{title} {jd_text}".lower()

    # Anti-types first (their keywords overlap the good ones).
    if any(w in text for w in (
            "sdr", "bdr", "sales development", "business development representative",
            "sales development rep")):
        return "sdr_bdr"
    if any(w in text for w in (
            "customer success", "renewals", "account manager", "csm",
            "retention specialist")):
        return "customer_success"

    # Good types.
    if any(w in text for w in (
            "strategic growth", "head of growth", "vp growth", "growth strategy",
            "director of growth", "revenue leader", "head of revenue")):
        return "strategic_growth"
    if any(w in text for w in (
            "partnership", "partnerships", "channel", "alliances", "business development"
            )) and "representative" not in text:
        return "partnerships"
    if any(w in text for w in (
            "account executive", "ae ", "sales executive", "enterprise sales",
            "quota", "closing", "full-cycle", "full cycle")):
        return "ae"
    return "unknown"


# ---------------------------------------------------------------------------
# Simplify status mapper (locked 2026-08-25)

_SIMPLIFY_STATUS_MAP = {
    "APPLIED": "applied",
    "INTERVIEWING": "interviewing",
    "OFFER": "interviewing",
    "REJECTED": "closed",
    "WITHDRAWN": "closed",
    "ARCHIVED": "closed",
}


def map_simplify_status(status: str) -> str:
    """Map Simplify status string to Banks internal status."""
    return _SIMPLIFY_STATUS_MAP.get(status.upper(), "sourced")
