"""Target watchlist (MOD-01, item 6) — Josh's priority companies.

A passive fit-score boost: when a *posted* opportunity's company matches a listed
target, its fit score gets a graded bump so the company floats up the queue. This
is NOT proactive surfacing — Banks still only acts on real postings (client:
"primary mode is applying to real postings"). The watchlist just ranks them.

Matched on the normalised company slug — the same normalisation the exclusion
wall uses — so casing/suffix differences ("EliseAI" vs "Elise AI, Inc.") still
match. Seeded from targets.txt at startup (container.live), mirroring exclusions.

Priority → bump:  1 (strong fit) +12 · 2 (moderate) +8 · 3 (breadth) +4.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .normalise import normalise_company
from .score import TARGET_BONUS  # single source of the graded bump values
from .store import cursor


def add_target(db_path: str, company: str, priority: int = 2,
               label: str | None = None) -> None:
    """Add (or update) a target company. Idempotent by normalised slug."""
    slug = normalise_company(company)
    if not slug:
        return
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO target_companies (company_normalized, priority, label, added_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(company_normalized) DO UPDATE SET "
            "priority = excluded.priority, label = excluded.label",
            (slug, priority, label or company, now),
        )


def target_priority(db_path: str, company: str) -> int | None:
    """The priority (1/2/3) if the company is on the watchlist, else None.

    Matches on the space-stripped normalised slug so camelCase vs spaced spellings
    reconcile ("EliseAI" ↔ "Elise AI") — job boards write company names either way.
    """
    slug = normalise_company(company)
    if not slug:
        return None
    nospace = slug.replace(" ", "")
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT priority FROM target_companies "
            "WHERE REPLACE(company_normalized, ' ', '') = ?",
            (nospace,),
        ).fetchone()
    return row["priority"] if row else None


def target_bonus(db_path: str, company: str) -> int:
    """The score bump for a company (0 if not a target)."""
    p = target_priority(db_path, company)
    return TARGET_BONUS.get(p, 0) if p is not None else 0


def load_targets_from_file(db_path: str, path: str) -> int:
    """Seed target_companies from a directive file. Idempotent (safe to re-run).

    Format (one per line, `#` comments and blanks ignored):
        priority 1: EliseAI
        priority 2: Kiavi
        priority 3: ClickPay

    A bare line with no `priority N:` prefix defaults to priority 2. Returns the
    number of targets loaded.
    """
    if not os.path.exists(path):
        return 0
    loaded = 0
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            priority = 2
            name = line
            if ":" in line:
                head, _, tail = line.partition(":")
                head = head.strip().lower()
                if head.startswith("priority"):
                    digits = head.replace("priority", "").strip()
                    if digits in ("1", "2", "3"):
                        priority = int(digits)
                        name = tail.strip()
            if name:
                add_target(db_path, name, priority)
                loaded += 1
    return loaded
