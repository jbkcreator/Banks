"""Verified contact enrichment (MOD-02).

The cold-company half of contact resolution: when a Tier A/B opportunity
surfaces and Josh knows nobody there, find the requisition owner (VP Sales / CRO
/ Head of Growth — not generic HR) and a verified email, so the outreach lane has
a real human to reach.

Design (grilled + locked 2026-08-25):
- Provider is Clay. Its API is batch/async and paid-only, so everything sits
  behind a batch EnrichmentPort: submit(list) -> batch_id, retrieve(batch_id) ->
  results (or None while pending). Three implementations:
    * FakeEnrichmentPort       — instant deterministic results (tests)
    * ManualCSVEnrichmentPort  — writes needs_enrichment.csv, reads the enriched
                                 file back; runnable on Clay's FREE tier by hand
    * LiveClayEnrichmentPort    — paid push+poll; inert until the plan is upgraded
- verified→email lane, unverified/none→LinkedIn DM draft (routing happens at
  draft time in MOD-03; here we just set the `verified` flag).
- Results cache in `contacts` (source='clay_enrichment') with a 30-day TTL so the
  same person is never looked up twice.
- A cold Tier A/B opportunity enqueues its company; two jobs drain the queue.

Hard wall intact: no FA imports; credentials via load_config().
"""
from __future__ import annotations

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


class LiveClayEnrichmentPort:
    """Paid Clay path: push a batch into a Clay table, poll for enriched rows.

    Inert until the account is on a paid plan with a configured table + webhook
    (CLIENT_QUERIES_V2 item 5). Requires clay_api_key + a table webhook URL.
    """
    _BASE = "https://api.clay.com/v3"

    def submit(self, requests: list[EnrichmentRequest]) -> str:
        cfg = load_config()
        if not cfg.clay_api_key:
            raise RuntimeError(
                "LiveClayEnrichmentPort needs a PAID Clay plan + table webhook. "
                "Free tier blocks the API — use ManualCSVEnrichmentPort until upgraded "
                "(see CLIENT_QUERIES_V2 item 5).")
        # Real impl: POST each request into the Clay table webhook. Left guarded
        # until a paid table URL exists so we never pretend to have enriched.
        raise NotImplementedError("Clay paid table webhook not provisioned yet.")

    def retrieve(self, batch_id: str) -> list[EnrichmentResult] | None:
        raise NotImplementedError("Clay paid table poll not provisioned yet.")


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
    reqs = [EnrichmentRequest(company=r["company_normalized"], role_hint=r["role_hint"])
            for r in rows]
    batch_id = port.submit(reqs)
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE enrichment_queue SET status='submitted', batch_id=? "
            "WHERE status='pending'", (batch_id,))
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
            if res and res.email:
                cur.execute(
                    "INSERT INTO contacts (name, company, email, linkedin_url, degree, "
                    "source, title, verified, enriched_at, added_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                    (res.name, q["company_normalized"], res.email, res.linkedin_url,
                     _CLAY_SOURCE, res.title, 1 if res.verified else 0, _now(), _now()))
                contact_id = cur.lastrowid
                written += 1
                if q["opportunity_id"]:
                    cur.execute("UPDATE opportunities SET contact_id=? WHERE id=?",
                                (contact_id, q["opportunity_id"]))
            cur.execute("UPDATE enrichment_queue SET status='done', resolved_at=? WHERE id=?",
                        (_now(), q["id"]))
    return written
