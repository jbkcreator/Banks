"""Export Banks contacts to a CSV ready for Clay Audiences import.

Usage:
    python scripts/export_contacts_clay.py [--out PATH]

Defaults to clay_contacts_YYYYMMDD.csv in the project root.
Import the file into Clay: Audiences → Import CSV → map columns.

Column mapping for Clay:
    name          → Full Name
    company       → Company
    email         → Work Email
    linkedin_url  → LinkedIn URL
    title         → Job Title
    source        → (custom field: Banks Source)
    verified      → (custom field: Email Verified)
"""
import argparse
import csv
import os
import pathlib
import sys
from datetime import datetime

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.split("#")[0].strip()

from banks.store import cursor, init_db  # noqa: E402
from banks.config import load_config     # noqa: E402

CLAY_COLUMNS = [
    "name", "company", "email", "linkedin_url",
    "title", "source", "verified", "enriched_at",
]


def export(db_path: str, out_path: str) -> int:
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT name, company, email, linkedin_url, title, source, "
            "verified, enriched_at FROM contacts "
            "WHERE name IS NOT NULL AND name != '' "
            "ORDER BY enriched_at DESC, added_at DESC"
        ).fetchall()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CLAY_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "name": r["name"] or "",
                "company": r["company"] or "",
                "email": r["email"] or "",
                "linkedin_url": r["linkedin_url"] or "",
                "title": r["title"] or "",
                "source": r["source"] or "",
                "verified": "yes" if r["verified"] else "no",
                "enriched_at": r["enriched_at"] or "",
            })
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Export contacts to Clay CSV")
    parser.add_argument("--out", default=None, help="Output CSV path")
    args = parser.parse_args()

    cfg = load_config()
    db_path = cfg.db_path
    init_db(db_path)

    out = args.out or str(
        root / f"clay_contacts_{datetime.now().strftime('%Y%m%d')}.csv"
    )
    count = export(db_path, out)
    print(f"Exported {count} contacts -> {out}")
    print("Next: Clay -> Audiences -> Import CSV -> map columns")


if __name__ == "__main__":
    main()
