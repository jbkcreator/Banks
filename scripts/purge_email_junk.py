"""Purge junk opportunities created by the OLD email-discovery bug.

Before the match-and-confirm rewrite (commit e26d8e7), ingest_email_confirmations
scanned the whole inbox and CREATED an opportunity for anything matching an
"application" keyword — so marketing mail (HELOC, coupons, Wayfair, Experian…)
became fake opportunities with source='email_confirmation' and
title='(from forwarded confirmation)'. The rewrite stopped NEW junk but never
purged the OLD rows. This script removes them.

Usage (on the server, in the repo, venv active):
    python -m scripts.purge_email_junk              # dry-run: lists what WOULD go
    python -m scripts.purge_email_junk --purge      # actually delete

Targets ONLY rows that carry BOTH the old junk source AND the old junk title
placeholder — the exact fingerprint the old create-from-email path stamped on
every row (`record_opportunity(..., "email_confirmation", ...)` with
`title="(from forwarded confirmation)"`). Deleting by source ALONE is unsafe
because a real confirmation the old path happened to ingest would share that
source; requiring the placeholder title guarantees we only remove rows that never
carried real role data. Real opportunities from the CSV (source='simplify') and
manual JD uploads (source='manual') are untouched, and so is any email row that
somehow has a real title.
"""
from __future__ import annotations

import sys

from banks.config import load_config
from banks.store import cursor

_JUNK_SOURCE = "email_confirmation"
_JUNK_TITLE = "(from forwarded confirmation)"


def main() -> int:
    purge = "--purge" in sys.argv
    cfg = load_config()
    db_path = cfg.db_path

    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT id, title, company_normalized, status FROM opportunities "
            "WHERE source = ? AND title = ?", (_JUNK_SOURCE, _JUNK_TITLE)
        ).fetchall()

    if not rows:
        print(f"No rows matching source='{_JUNK_SOURCE}' AND title='{_JUNK_TITLE}' "
              f"— nothing to purge.")
        return 0

    print(f"Found {len(rows)} junk opportunit{'y' if len(rows)==1 else 'ies'} "
          f"(source='{_JUNK_SOURCE}', title='{_JUNK_TITLE}'):")
    for r in rows:
        print(f"  [{r['id']}] {r['company_normalized']!r} — {r['title']!r} "
              f"({r['status']})")

    if not purge:
        print("\nDRY RUN — nothing deleted. Re-run with --purge to delete these.")
        return 0

    ids = [r["id"] for r in rows]
    with cursor(db_path) as cur:
        q = ",".join("?" * len(ids))
        cur.execute(f"DELETE FROM opportunities WHERE id IN ({q})", ids)
    print(f"\nDeleted {len(ids)} junk rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
