"""Opportunity enrichment from the Job URL (MOD-01/02).

Simplify (and manual-URL) rows arrive without salary or industry, so they're
recorded with needs_enrichment=1 and held back from Slack (Decision 4). This
module closes that gap the way the plan intends — by reading the posting itself:

    fetch the Job URL -> strip to text -> Claude extracts industry + comp regex
    pulls the base -> re-score -> if it now clears Tier A/B, surface it.

No Clay needed: the posting *is* the enrichment source for job data. (Clay was
only ever for finding a contact's email — a separate problem.)

Fetching is behind a Port so tests never hit the network.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Protocol

import httpx

from . import score as _score
from .chatport import ChatPort
from .intake import _surface_opportunity
from .llmport import LLMPort
from .manual_intake import _JD_EXTRACT_SYSTEM, extract_comp_k
from .opportunity import mark_application_drafted
from .store import cursor

_UA = "Mozilla/5.0 (compatible; BanksBot/1.0; job-posting reader)"
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    html = _SCRIPT.sub(" ", html)
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


class FetchPort(Protocol):
    def fetch(self, url: str) -> str | None: ...


class FakeFetchPort:
    """Canned pages for tests — no network."""
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def fetch(self, url: str) -> str | None:
        return self._pages.get(url)


def _is_safe_url(url: str) -> bool:
    """SSRF guard: https-only, no private/loopback/reserved IPs."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    host = parsed.hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except Exception:
        return False
    return True


class LiveFetchPort:
    """Real HTTP GET + HTML-to-text. SSRF-safe: https-only, no private IPs, max 3 redirects."""
    def fetch(self, url: str) -> str | None:
        try:
            for _ in range(3):
                if not _is_safe_url(url):
                    return None
                r = httpx.get(url, timeout=15, follow_redirects=False,
                              headers={"User-Agent": _UA})
                if r.status_code in (301, 302, 303, 307, 308):
                    url = r.headers.get("location", "")
                    continue
                r.raise_for_status()
                return html_to_text(r.text)[:8000]
        except Exception:
            return None
        return None


@dataclass(frozen=True)
class EnrichResult:
    opportunity_id: int
    outcome: str        # "surfaced" | "still_held" | "fetch_failed" | "no_url"
    tier: str
    fit: int


def enrich_opportunity(
    db_path: str,
    opp_id: int,
    fetch: FetchPort,
    llm: LLMPort,
    chat: ChatPort,
    *,
    surface_tiers: tuple[str, ...] = ("A", "B"),
) -> EnrichResult:
    """Read one opportunity's posting, re-score, and surface if it clears A/B."""
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT id, title, company_normalized, source_url, pursuit_mode "
            "FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
    if row is None:
        return EnrichResult(opp_id, "no_url", "-", 0)
    url = row["source_url"]
    if not url:
        return EnrichResult(opp_id, "no_url", "-", 0)

    text = fetch.fetch(url)
    if not text:
        return EnrichResult(opp_id, "fetch_failed", "-", 0)

    comp_k = extract_comp_k(text)
    ex = llm.extract_json(_JD_EXTRACT_SYSTEM, text[:6000])
    industry = ex.get("industry")
    location = ex.get("location") or ""

    pursuit_mode = row["pursuit_mode"] or "full_time"
    fit, tier, needs_enrichment = _score.score_role(
        comp_k=comp_k, industry=industry, location=location, pursuit_mode=pursuit_mode)

    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE opportunities SET criteria_match_score=?, tier=?, "
            "needs_enrichment=?, industry=? WHERE id=?",
            (fit, tier, 1 if needs_enrichment else 0, industry, opp_id),
        )

    if needs_enrichment:
        return EnrichResult(opp_id, "still_held", tier, fit)

    if tier in surface_tiers:
        parsed = {"title": row["title"], "company": row["company_normalized"],
                  "location": location, "industry": industry}
        _surface_opportunity(db_path, chat, opp_id, parsed, fit, tier, pursuit_mode)
        mark_application_drafted(db_path, opp_id)
        return EnrichResult(opp_id, "surfaced", tier, fit)

    return EnrichResult(opp_id, "still_held", tier, fit)


@dataclass(frozen=True)
class EnrichBatch:
    processed: int
    surfaced: int
    still_held: int
    fetch_failed: int
    results: list[EnrichResult]


def enrich_pending(
    db_path: str,
    fetch: FetchPort,
    llm: LLMPort,
    chat: ChatPort,
    *,
    limit: int | None = None,
) -> EnrichBatch:
    """Enrich every held (needs_enrichment=1) opportunity that has a URL."""
    with cursor(db_path) as cur:
        q = ("SELECT id FROM opportunities "
             "WHERE needs_enrichment = 1 AND source_url IS NOT NULL ORDER BY id")
        ids = [r["id"] for r in cur.execute(q).fetchall()]
    if limit:
        ids = ids[:limit]

    results = [enrich_opportunity(db_path, i, fetch, llm, chat) for i in ids]
    surfaced = sum(r.outcome == "surfaced" for r in results)
    held = sum(r.outcome == "still_held" for r in results)
    failed = sum(r.outcome == "fetch_failed" for r in results)
    return EnrichBatch(len(results), surfaced, held, failed, results)
