"""Opportunity dedup: URL exact match first, fuzzy company+title fallback."""
from __future__ import annotations

import re

from banks.store import db


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find_duplicate(db_path: str, source_url: str | None, title: str, company: str) -> int | None:
    """Return existing opportunity id if duplicate, else None.

    Pass 1: exact source_url match (fastest, most reliable).
    Pass 2: fuzzy slug match on (company_normalized, title).
    """
    if source_url:
        with db.cursor(db_path) as cur:
            row = cur.execute(
                "SELECT id FROM opportunities WHERE source_url = ?", (source_url,)
            ).fetchone()
            if row:
                return row["id"]

    title_slug = _slug(title)
    company_slug = _slug(company)
    with db.cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT id, title, company_normalized FROM opportunities"
        ).fetchall()
    for row in rows:
        if (
            _slug(row["company_normalized"] or "") == company_slug
            and _slug(row["title"]) == title_slug
        ):
            return row["id"]
    return None
