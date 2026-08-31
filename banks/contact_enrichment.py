"""Verified contact enrichment (MOD-02).

The cold-company half of contact resolution: when a Tier A/B opportunity
surfaces and Josh knows nobody there, find the requisition owner (VP Sales / CRO
/ Head of Growth — not generic HR) and a verified email, so the outreach lane has
a real human to reach.

Design (grilled + locked 2026-08-25, updated 2026-08-31):
- Provider is Clay. Everything sits behind a batch EnrichmentPort:
  submit(list) -> batch_id, retrieve(batch_id) -> results (or None while pending).
  Four implementations:
    * FakeEnrichmentPort       — instant deterministic results (tests)
    * ManualCSVEnrichmentPort  — writes needs_enrichment.csv, reads the enriched
                                 file back; runnable on Clay's FREE tier by hand
    * LiveClaySearchPort       — Clay Search API (Launch plan compatible); finds
                                 contacts by company+title via GTM database search;
                                 needs only BANKS_CLAY_API_KEY
    * LiveClayEnrichmentPort   — original webhook+Sheet design; requires Growth plan
- verified→email lane, unverified/none→LinkedIn DM draft (routing happens at
  draft time in MOD-03; here we just set the `verified` flag).
- Results cache in `contacts` (source='clay_enrichment') with a 30-day TTL so the
  same person is never looked up twice.
- A cold Tier A/B opportunity enqueues its company; two jobs drain the queue.

Hard wall intact: no FA imports; credentials via load_config().
"""
from __future__ import annotations

import json

import csv
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from .config import load_config
from .store import cursor
from .warmpath import attach_contact

CACHE_TTL_DAYS = 30
_CLAY_SOURCE = "clay_enrichment"


# --- data ------------------------------------------------------------------

@dataclass(frozen=True)
class EnrichmentRequest:
    company: str
    role_hint: str | None = None
    name: str | None = None          # known (warm) -> skip discovery, resolve email only


@dataclass(frozen=True)
class EnrichmentResult:
    company: str
    name: str
    email: str
    verified: bool
    title: str | None = None
    linkedin_url: str = ""


# --- Port ------------------------------------------------------------------

class EnrichmentPort(Protocol):
    def submit(self, requests: list[EnrichmentRequest]) -> str: ...
    def retrieve(self, batch_id: str) -> list[EnrichmentResult] | None: ...


class FakeEnrichmentPort:
    """Instant, deterministic — for tests. Results ready on first retrieve."""
    def __init__(self, results: dict[str, list[EnrichmentResult]] | None = None) -> None:
        self._scripted = results or {}
        self._batches: dict[str, list[EnrichmentRequest]] = {}

    def submit(self, requests: list[EnrichmentRequest]) -> str:
        bid = f"fake-{uuid.uuid4().hex[:8]}"
        self._batches[bid] = list(requests)
        return bid

    def retrieve(self, batch_id: str) -> list[EnrichmentResult] | None:
        reqs = self._batches.get(batch_id, [])
        out: list[EnrichmentResult] = []
        for r in reqs:
            if r.company in self._scripted:
                out.extend(self._scripted[r.company])
            else:
                out.append(EnrichmentResult(
                    company=r.company, name=r.name or "Test Owner",
                    email=f"owner@{r.company.replace(' ', '')}.com",
                    verified=True, title=r.role_hint or "VP", linkedin_url=""))
        return out


class ManualCSVEnrichmentPort:
    """Free-tier interim: submit writes a CSV for a human to run through Clay's UI;
    retrieve reads the returned enriched CSV once dropped back. $0, hands-on."""
    def __init__(self, out_dir: str = "enrichment") -> None:
        self._dir = out_dir
        os.makedirs(out_dir, exist_ok=True)

    def _req_path(self, bid: str) -> str:
        return os.path.join(self._dir, f"needs_enrichment_{bid}.csv")

    def _res_path(self, bid: str) -> str:
        return os.path.join(self._dir, f"enriched_{bid}.csv")

    def submit(self, requests: list[EnrichmentRequest]) -> str:
        bid = uuid.uuid4().hex[:8]
        with open(self._req_path(bid), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["company", "role_hint", "name"])
            for r in requests:
                w.writerow([r.company, r.role_hint or "", r.name or ""])
        return bid

    def retrieve(self, batch_id: str) -> list[EnrichmentResult] | None:
        path = self._res_path(batch_id)
        if not os.path.exists(path):
            return None  # human hasn't dropped the enriched file back yet
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return [EnrichmentResult(
            company=r.get("company", ""), name=r.get("name", ""),
            email=r.get("email", ""),
            verified=str(r.get("verified", "")).strip().lower() in ("1", "true", "yes", "verified"),
            title=r.get("title") or None, linkedin_url=r.get("linkedin_url", "")) for r in rows]


class LiveClaySearchPort:
    """Clay Search API — Launch plan compatible, no webhook or Sheet needed.

    Uses Clay's GTM people-search database (POST /search/filters-mode) to find
    contacts by company + title keywords. No inbound webhook or Google Sheet buffer
    required — just BANKS_CLAY_API_KEY.

    submit()   — creates one Clay search per company; stores a JSON map of
                 {company: search_id} as the batch_id string.
    retrieve() — runs each search's iterator (3 results, take first), maps to
                 EnrichmentResult. Returns immediately — search is synchronous.
                 email is empty (search API returns LinkedIn only); downstream
                 routing will choose the LinkedIn DM lane.
    """
    _BASE = "https://api.clay.com/public/v0"
    _DEFAULT_TITLES = [
        "VP Sales", "CRO", "Chief Revenue Officer", "Head of Sales",
        "VP of Sales", "Director of Sales", "VP Revenue",
    ]

    def __init__(self, cfg=None) -> None:
        self._cfg = cfg or load_config()
        if not self._cfg.clay_api_key:
            raise RuntimeError("LiveClaySearchPort needs BANKS_CLAY_API_KEY")
        self._headers = {
            "clay-api-key": self._cfg.clay_api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _to_domain(company: str) -> str:
        """Best-effort company name → domain. e.g. 'HubSpot' → 'hubspot.com'."""
        import re
        slug = re.sub(r"[^a-z0-9]", "", company.lower())
        return f"{slug}.com"

    def _title_keywords(self, role_hint: str | None) -> list[str]:
        if not role_hint:
            return self._DEFAULT_TITLES
        words = [w.strip() for w in role_hint.split() if len(w.strip()) > 2]
        return words or self._DEFAULT_TITLES

    def submit(self, requests: list[EnrichmentRequest]) -> str:
        mapping: dict[str, str] = {}
        for req in requests:
            payload = {
                "source_type": "people",
                "filters": {
                    "company_identifier": [self._to_domain(req.company)],
                    "job_title_keywords": self._title_keywords(req.role_hint),
                },
            }
            resp = httpx.post(
                f"{self._BASE}/search/filters-mode",
                headers=self._headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            mapping[req.company] = resp.json()["search_id"]
        return json.dumps(mapping)

    def retrieve(self, batch_id: str) -> list[EnrichmentResult] | None:
        try:
            mapping: dict[str, str] = json.loads(batch_id)
        except (ValueError, TypeError):
            return None
        results: list[EnrichmentResult] = []
        for company, search_id in mapping.items():
            resp = httpx.post(
                f"{self._BASE}/search/filters-mode/{search_id}/run",
                headers=self._headers,
                json={"limit": 3},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            for row in resp.json().get("data", []):
                name = row.get("name", "")
                linkedin_url = row.get("url", "")
                if not (name and linkedin_url):
                    continue
                title = (row.get("latest_experience_title")
                         or (row.get("matched_experience") or {}).get("job_title"))
                results.append(EnrichmentResult(
                    company=company, name=name, email="",
                    verified=False, title=title, linkedin_url=linkedin_url,
                ))
                break  # first usable contact per company
        return results or None


class LiveClayEnrichmentPort:
    """Paid Clay path, push-in / Sheet-pull (grilled 2026-08-29).

    Clay's paid API is push-oriented and async: there is no synchronous "enrich
    and poll" endpoint. So the two halves talk to different endpoints but form
    one logical port:

      submit()   — POSTs each queued company into a Clay table via its inbound
                   webhook (`clay_webhook_url`), tagging every row with a
                   generated batch_id so results can be correlated later.
      retrieve() — reads the enriched rows Clay writes to a Google Sheet buffer
                   (`enrichment_sheet_id`) with a READ-ONLY service account, and
                   returns the rows whose batch_id matches. None while the Sheet
                   has no rows for the batch yet (still pending).

    The Sheet is the async buffer that decouples Clay's push from Banks' pull —
    and it means Banks needs no inbound network surface, so the hard wall stays
    physical (outbound POST + outbound read only). Reuses the same service-account
    pattern as GoogleCalendarPort.

    Inert until BOTH `clay_webhook_url` and `enrichment_sheet_id` are set; until
    then submit() raises clearly rather than pretending to have enriched.

    `sheet_reader` is an injection seam: a zero-arg callable returning the Sheet's
    rows as list[dict] (header-keyed). Defaults to a live read-only Sheets read;
    tests pass a fake so retrieve() parsing is exercised with no network.
    """
    _BATCH_COL = "batch_id"

    def __init__(self, cfg=None, sheet_reader=None) -> None:
        self._cfg = cfg or load_config()
        self._sheet_reader = sheet_reader

    def submit(self, requests: list[EnrichmentRequest]) -> str:
        if not self._cfg.clay_webhook_url:
            raise RuntimeError(
                "LiveClayEnrichmentPort needs BANKS_CLAY_WEBHOOK_URL (a Clay table "
                "inbound webhook) + BANKS_ENRICHMENT_SHEET_ID. Until both are set, "
                "use ManualCSVEnrichmentPort — never pretend to have enriched.")
        batch_id = f"clay-{uuid.uuid4().hex[:8]}"
        for r in requests:
            payload = {
                self._BATCH_COL: batch_id,
                "company": r.company,
                "role_hint": r.role_hint or "",
                "name": r.name or "",
            }
            httpx.post(self._cfg.clay_webhook_url, json=payload, timeout=30).raise_for_status()
        return batch_id

    def _read_sheet(self) -> list[dict]:
        """Read the enrichment Sheet as header-keyed rows via a read-only SA."""
        if self._sheet_reader is not None:
            return self._sheet_reader()
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            self._cfg.gcp_sa_key,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        svc = build("sheets", "v4", credentials=creds)
        resp = svc.spreadsheets().values().get(
            spreadsheetId=self._cfg.enrichment_sheet_id,
            range=self._cfg.enrichment_sheet_range).execute()
        values = resp.get("values", [])
        if not values:
            return []
        header, *rows = values
        return [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows]

    def retrieve(self, batch_id: str) -> list[EnrichmentResult] | None:
        rows = [r for r in self._read_sheet() if r.get(self._BATCH_COL) == batch_id]
        if not rows:
            return None  # Clay hasn't written this batch back to the Sheet yet
        return [EnrichmentResult(
            company=r.get("company", ""), name=r.get("name", ""),
            email=r.get("email", ""),
            verified=str(r.get("verified", "")).strip().lower() in ("1", "true", "yes", "verified"),
            title=r.get("title") or None, linkedin_url=r.get("linkedin_url", "")) for r in rows]


# --- queue -----------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_fresh_enrichment(db_path: str, company_normalized: str) -> bool:
    """True if we already enriched this company within the TTL (skip re-spend)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)).isoformat()
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT 1 FROM contacts WHERE company = ? AND source = ? "
            "AND enriched_at IS NOT NULL AND enriched_at >= ? LIMIT 1",
            (company_normalized, _CLAY_SOURCE, cutoff),
        ).fetchone()
    return row is not None


def enqueue_company(db_path: str, company_normalized: str, role_hint: str | None,
                    opportunity_id: int | None) -> bool:
    """Queue a cold company for enrichment. Skips if fresh-cached or already queued.
    Returns True if newly enqueued."""
    if has_fresh_enrichment(db_path, company_normalized):
        return False
    with cursor(db_path) as cur:
        dup = cur.execute(
            "SELECT 1 FROM enrichment_queue WHERE company_normalized = ? "
            "AND status IN ('pending','submitted') LIMIT 1", (company_normalized,)
        ).fetchone()
        if dup:
            return False
        cur.execute(
            "INSERT INTO enrichment_queue "
            "(company_normalized, role_hint, opportunity_id, status, requested_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (company_normalized, role_hint, opportunity_id, _now()),
        )
    return True


# --- jobs ------------------------------------------------------------------

def submit_pending(db_path: str, port: EnrichmentPort) -> str | None:
    """Drain pending queue rows into one batch. Returns batch_id, or None if empty."""
    with cursor(db_path) as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT id, company_normalized, role_hint FROM enrichment_queue "
            "WHERE status = 'pending'"
        ).fetchall()]
    if not rows:
        return None
    ids = [r["id"] for r in rows]
    reqs = [EnrichmentRequest(company=r["company_normalized"], role_hint=r["role_hint"])
            for r in rows]
    batch_id = port.submit(reqs)
    # Mark ONLY the rows we actually submitted. A cold opportunity queued while
    # the webhook push was in flight must stay 'pending' for the next batch —
    # updating every pending row would mark it submitted without ever sending it,
    # then retrieve finds no result and fails it (lost forever). Concurrent
    # writers (scheduler + Socket listener) make this window real.
    placeholders = ",".join("?" * len(ids))
    with cursor(db_path) as cur:
        cur.execute(
            f"UPDATE enrichment_queue SET status='submitted', batch_id=? "
            f"WHERE id IN ({placeholders})", (batch_id, *ids))
    return batch_id


def retrieve_and_apply(db_path: str, port: EnrichmentPort, batch_id: str) -> int:
    """Retrieve a submitted batch; write verified/unverified contacts, re-attach to
    the triggering opportunity. Returns count of contacts written. No-op if pending."""
    results = port.retrieve(batch_id)
    if results is None:
        return 0  # still pending
    by_company: dict[str, EnrichmentResult] = {}
    for r in results:
        by_company.setdefault(r.company, r)  # first result per company

    written = 0
    with cursor(db_path) as cur:
        queued = [dict(x) for x in cur.execute(
            "SELECT id, company_normalized, opportunity_id FROM enrichment_queue "
            "WHERE batch_id = ? AND status = 'submitted'", (batch_id,)).fetchall()]

    for q in queued:
        res = by_company.get(q["company_normalized"])
        with cursor(db_path) as cur:
            # Persist if we have a name + either email or LinkedIn URL.
            # verified=0 when no email — downstream routing will choose LinkedIn DM.
            if res and (res.email or res.linkedin_url):
                cur.execute(
                    "INSERT INTO contacts (name, company, email, linkedin_url, degree, "
                    "source, title, verified, enriched_at, added_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                    (res.name, q["company_normalized"], res.email or "", res.linkedin_url,
                     _CLAY_SOURCE, res.title, 1 if res.verified else 0, _now(), _now()))
                contact_id = cur.lastrowid
                written += 1
                if q["opportunity_id"]:
                    cur.execute("UPDATE opportunities SET contact_id=? WHERE id=?",
                                (contact_id, q["opportunity_id"]))
                cur.execute("UPDATE enrichment_queue SET status='done', resolved_at=? WHERE id=?",
                            (_now(), q["id"]))
            else:
                # No usable result — mark failed so it can be retried or flagged.
                cur.execute("UPDATE enrichment_queue SET status='failed', resolved_at=? WHERE id=?",
                            (_now(), q["id"]))
    return written


def drain_submitted(db_path: str, port: EnrichmentPort) -> int:
    """Retrieve every outstanding submitted batch. Returns total contacts written.

    The scheduled retrieve job: finds each distinct batch still 'submitted' and
    runs retrieve_and_apply. Batches Clay hasn't written back yet are no-ops
    (retrieve returns None), so this is safe to fire on a short interval.
    """
    with cursor(db_path) as cur:
        batch_ids = [r["batch_id"] for r in cur.execute(
            "SELECT DISTINCT batch_id FROM enrichment_queue "
            "WHERE status = 'submitted' AND batch_id IS NOT NULL").fetchall()]
    return sum(retrieve_and_apply(db_path, port, bid) for bid in batch_ids)


def select_enrichment_port(cfg) -> EnrichmentPort | None:
    """Choose the live enrichment port from config, or None if unprovisioned.

    Priority:
      1. LiveClaySearchPort    — just needs BANKS_CLAY_API_KEY (Launch plan)
      2. LiveClayEnrichmentPort — webhook+Sheet path (requires Growth plan)
      3. None                  — jobs no-op; manual CSV path still available
    """
    if cfg.clay_api_key:
        return LiveClaySearchPort(cfg)
    if cfg.clay_webhook_url and cfg.enrichment_sheet_id:
        return LiveClayEnrichmentPort(cfg)
    return None
