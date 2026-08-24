"""ClayPort: contact enrichment via Clay API.

Fake returns deterministic stubs; Live calls api.clay.com.
Hard-walled: zero FA imports. Credentials via load_config().
"""
from __future__ import annotations

import os
from typing import Protocol

import httpx

_CLAY_BASE = "https://api.clay.com/v1"


class ClayPort(Protocol):
    def enrich(self, company: str, name: str | None = None) -> dict: ...


class FakeClayPort:
    """Deterministic stub for tests — no network, no credentials."""

    def enrich(self, company: str, name: str | None = None) -> dict:
        return {
            "name": name or "Test Contact",
            "email": f"test@{company.lower().replace(' ', '')}.com",
            "linkedin_url": "",
            "headcount": None,
        }


class LiveClayPort:
    """Calls api.clay.com/v1/sources/enrichment. Requires BANKS_CLAY_API_KEY."""

    def enrich(self, company: str, name: str | None = None) -> dict:
        api_key = os.environ.get("BANKS_CLAY_API_KEY", "")
        payload: dict = {"company_name": company}
        if name:
            payload["full_name"] = name
        try:
            resp = httpx.post(
                f"{_CLAY_BASE}/sources/enrichment",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "name": data.get("full_name") or name or "",
                "email": data.get("email", ""),
                "linkedin_url": data.get("linkedin_url", ""),
                "headcount": data.get("headcount"),
            }
        except Exception:
            return {"name": name or "", "email": "", "linkedin_url": "", "headcount": None}
