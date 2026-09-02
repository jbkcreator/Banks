"""Find a work email via Clay, on demand (MOD-02 extension, 2026-09-02).

Why this is separate from contact_enrichment.LiveClaySearchPort: the Search API
(`/search/filters-mode`) finds the right *person* at a company — name, title,
LinkedIn URL — and verified working against Josh's paid account, but its rows
carry **no email field**. So search alone can never populate the email lane.

Clay has no generic "find work email" REST endpoint either. Enrichment runs as a
**Routine** built in Clay's UI and invoked by id:

    POST /routines/{routine_id}/run   {"items": [{"id": ..., "inputs": {...}}]}
      -> 202 {"routine_run_id": ..., "status": "in_progress"}
    GET  /routines/runs/{routine_run_id}
      -> results once complete

So Banks needs BANKS_CLAY_EMAIL_ROUTINE_ID pointing at a routine that takes a
person (name / company domain / LinkedIn URL) and returns a work email. Without
it this module is inert and outreach keeps routing to the LinkedIn DM lane —
the same graceful degradation as before, never a silent failure.

Anything found here is written to `contacts.email` with verified=1 (a provider
vouched for it) so the email lane can fire on Josh's next approval.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

import httpx

from .store import cursor

_BASE = "https://api.clay.com/public/v0"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# Bounded so a draft is never blocked for long: Clay routines are async, and a
# slow lookup must fall back to the LinkedIn lane rather than stall the card.
POLL_TIMEOUT_S = 25
POLL_INTERVAL_S = 2.5

# Response keys Clay routines commonly use for the address. Checked in order.
_EMAIL_KEYS = ("work_email", "email", "professional_email", "business_email",
               "email_address", "primary_email")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def company_domain(company: str) -> str:
    """Best-effort company name -> domain ('Second Nature' -> 'secondnature.com')."""
    slug = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    return f"{slug}.com" if slug else ""


def _extract_email(payload) -> str:
    """Pull the first plausible address out of a routine result, shape-agnostic.

    Routine output shape depends on how the routine was built in Clay's UI, so
    this walks the structure rather than assuming one schema.
    """
    if isinstance(payload, str):
        return payload.strip() if _EMAIL_RE.match(payload.strip()) else ""
    if isinstance(payload, dict):
        for key in _EMAIL_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and _EMAIL_RE.match(val.strip()):
                return val.strip()
        for val in payload.values():
            found = _extract_email(val)
            if found:
                return found
        return ""
    if isinstance(payload, list):
        for val in payload:
            found = _extract_email(val)
            if found:
                return found
    return ""


class ClayEmailFinder:
    """Runs the configured Clay routine to resolve one person's work email."""

    def __init__(self, cfg=None) -> None:
        from .config import load_config
        self._cfg = cfg or load_config()

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.clay_api_key and self._cfg.clay_email_routine_id)

    def _headers(self) -> dict:
        return {"clay-api-key": self._cfg.clay_api_key,
                "Content-Type": "application/json"}

    def find(self, *, name: str, company: str, linkedin_url: str = "") -> str:
        """Return a work email, or "" if not found / not configured / timed out."""
        if not self.enabled:
            return ""
        inputs = {
            "full_name": name,
            "name": name,
            "company_name": company,
            "company_domain": company_domain(company),
            "domain": company_domain(company),
        }
        if linkedin_url:
            inputs["linkedin_url"] = linkedin_url
        try:
            resp = httpx.post(
                f"{_BASE}/routines/{self._cfg.clay_email_routine_id}/run",
                headers=self._headers(),
                json={"items": [{"id": "banks-1", "inputs": inputs}]},
                timeout=30,
            )
            if resp.status_code not in (200, 202):
                print(f"[clay] routine run failed {resp.status_code}: {resp.text[:200]}",
                      flush=True)
                return ""
            run_id = (resp.json() or {}).get("routine_run_id")
        except Exception as exc:
            print(f"[clay] routine run error: {exc!r}", flush=True)
            return ""
        if not run_id:
            return ""
        return self._poll(run_id)

    def _poll(self, run_id: str) -> str:
        deadline = time.monotonic() + POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{_BASE}/routines/runs/{run_id}",
                              headers=self._headers(), timeout=20)
                if r.status_code == 200:
                    body = r.json()
                    if str(body.get("status", "")).lower() in ("complete", "completed",
                                                               "succeeded", "done"):
                        return _extract_email(body)
                    found = _extract_email(body)
                    if found:
                        return found
            except Exception as exc:
                print(f"[clay] poll error: {exc!r}", flush=True)
                return ""
            time.sleep(POLL_INTERVAL_S)
        print(f"[clay] routine {run_id} still pending after {POLL_TIMEOUT_S}s — "
              f"falling back to the LinkedIn lane", flush=True)
        return ""


def ensure_contact_email(db_path: str, contact: dict, finder=None) -> dict:
    """Return `contact` with an email, looking it up via Clay if it has none.

    Writes any address found straight back to `contacts` with verified=1, so the
    lookup is paid for once and every later draft reuses it. On any failure the
    contact is returned unchanged and routing falls back to LinkedIn.
    """
    from .contacts import can_email

    if can_email(contact):
        return contact
    finder = finder or ClayEmailFinder()
    if not finder.enabled:
        return contact

    email = finder.find(
        name=contact.get("name") or "",
        company=contact.get("company") or "",
        linkedin_url=contact.get("linkedin_url") or "",
    )
    if not email:
        return contact

    cid = contact.get("id")
    if cid:
        with cursor(db_path) as cur:
            cur.execute(
                "UPDATE contacts SET email = ?, verified = 1, enriched_at = ? "
                "WHERE id = ?", (email, _now(), cid))
    print(f"[clay] found email for {contact.get('name')!r} at "
          f"{contact.get('company')!r}", flush=True)
    return {**contact, "email": email, "verified": 1}
