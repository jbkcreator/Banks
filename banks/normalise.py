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
