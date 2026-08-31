"""Live Clay enrichment smoke test — run on the server to verify end-to-end.

Usage:
    python scripts/test_clay_live.py

Uses a temporary DB (never touches production banks.db).
Requires BANKS_CLAY_API_KEY in .env or environment.
Prints PASS / FAIL per test; exits 1 if any fail.
"""
import os
import pathlib
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

env_file = root / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split("#")[0].strip())

from banks.config import load_config
from banks.contact_enrichment import (
    LiveClaySearchPort, EnrichmentRequest,
    enqueue_company, submit_pending, drain_submitted,
    has_fresh_enrichment, select_enrichment_port,
)
from banks.store import init_db, cursor

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name, ok, detail=""):
    tag = PASS if ok else FAIL
    line = f"  [{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    results.append((name, ok))


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


# ── setup ──────────────────────────────────────────────────────────────────
cfg = load_config()
db = tempfile.mktemp(suffix="_clay_test.db")
os.environ["BANKS_DB_PATH"] = db
init_db(db)


def seed_opp(company: str) -> int:
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO opportunities "
            "(title, company_normalized, source_url, source, status, "
            "pursuit_mode, tier, needs_enrichment, submitted) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("VP Sales", company, f"https://{company.lower()}.com/job/1",
             "test", "applied", "active", "A", 1, 0),
        )
        return cur.lastrowid


# ── 1. Auth ─────────────────────────────────────────────────────────────────
section("1. Auth")
check("BANKS_CLAY_API_KEY is set", bool(cfg.clay_api_key),
      f"key={'***set***' if cfg.clay_api_key else 'MISSING'}")

try:
    import httpx
    r = httpx.get("https://api.clay.com/public/v0/me",
                  headers={"clay-api-key": cfg.clay_api_key or ""}, timeout=10)
    check("Clay API reachable + key valid", r.status_code == 200,
          f"status={r.status_code}")
except Exception as e:
    check("Clay API reachable + key valid", False, str(e))

# ── 2. select_enrichment_port ───────────────────────────────────────────────
section("2. Port selection")
port = select_enrichment_port(cfg)
check("select_enrichment_port returns LiveClaySearchPort",
      isinstance(port, LiveClaySearchPort))

# ── 3. Domain heuristic ─────────────────────────────────────────────────────
section("3. Domain heuristic (company name -> domain)")
cases = [
    ("HubSpot",       "hubspot.com"),
    ("Second Nature", "secondnature.com"),
    ("Rent Solutions","rentsolutions.com"),
    ("Acme Corp.",    "acmecorp.com"),
]
for company, expected in cases:
    got = LiveClaySearchPort._to_domain(company)
    check(f'"{company}" -> "{expected}"', got == expected, f"got={got}")

# ── 4. Submit (single company) ──────────────────────────────────────────────
section("4. Submit — single company")
port = LiveClaySearchPort(cfg)
batch_id = None
try:
    batch_id = port.submit([EnrichmentRequest(company="HubSpot", role_hint="VP Sales")])
    import json
    mapping = json.loads(batch_id)
    check("submit returns JSON batch_id", True, f"search_id={mapping.get('HubSpot','?')[:20]}")
    check("batch_id contains HubSpot key", "HubSpot" in mapping)
except Exception as e:
    check("submit returns JSON batch_id", False, str(e))
    check("batch_id contains HubSpot key", False)

# ── 5. Submit (multi-company batch) ─────────────────────────────────────────
section("5. Submit — multiple companies")
batch_multi = None
try:
    batch_multi = port.submit([
        EnrichmentRequest(company="Salesforce", role_hint="CRO"),
        EnrichmentRequest(company="HubSpot", role_hint="VP Sales"),
    ])
    mapping = json.loads(batch_multi)
    check("two companies in batch_id", len(mapping) == 2, str(list(mapping.keys())))
except Exception as e:
    check("two companies in batch_id", False, str(e))

# ── 6. Retrieve (valid batch) ────────────────────────────────────────────────
section("6. Retrieve — valid batch")
if batch_id:
    try:
        results_clay = port.retrieve(batch_id)
        check("retrieve returns list (not None)", results_clay is not None)
        if results_clay:
            r = results_clay[0]
            check("result has name", bool(r.name), r.name)
            check("result has linkedin_url", bool(r.linkedin_url), r.linkedin_url[:40])
            check("result has company=HubSpot", r.company == "HubSpot", r.company)
            check("email is empty (search API)", r.email == "")
            check("verified=False (no email)", r.verified is False)
        else:
            check("result has name", False, "empty list returned")
    except Exception as e:
        check("retrieve returns list (not None)", False, str(e))

# ── 7. Retrieve — bad batch_id ──────────────────────────────────────────────
section("7. Retrieve — edge cases")
check("bad batch_id returns None", port.retrieve("not-json") is None)
check("empty JSON map returns None", port.retrieve("{}") is None)

# ── 8. Full DB pipeline ──────────────────────────────────────────────────────
section("8. Full DB pipeline: enqueue -> submit -> retrieve -> write")
opp_id = seed_opp("Notion")
queued = enqueue_company(db, "Notion", "VP Sales", opp_id)
check("enqueue_company returns True", queued is True)

bid = submit_pending(db, port)
check("submit_pending returns batch_id", bid is not None, bid)

with cursor(db) as cur:
    row = cur.execute("SELECT status, batch_id FROM enrichment_queue WHERE company_normalized='Notion'").fetchone()
check("queue status=submitted after submit_pending", row and row["status"] == "submitted")
check("batch_id stored in queue row", row and bool(row["batch_id"]))

written = drain_submitted(db, port)
check("drain_submitted writes >= 1 contact", written >= 1, f"written={written}")

with cursor(db) as cur:
    contact = cur.execute(
        "SELECT name, linkedin_url FROM contacts WHERE company='Notion' LIMIT 1"
    ).fetchone()
    opp = cur.execute("SELECT contact_id FROM opportunities WHERE id=?", (opp_id,)).fetchone()
    q = cur.execute("SELECT status FROM enrichment_queue WHERE company_normalized='Notion'").fetchone()

check("contact row in DB", contact is not None, dict(contact) if contact else "None")
check("opportunity linked to contact", opp and opp["contact_id"] is not None)
check("queue status=done", q and q["status"] == "done")

# ── 9. Idempotency ───────────────────────────────────────────────────────────
section("9. Idempotency")
opp2 = seed_opp("Stripe")
enqueue_company(db, "Stripe", "CRO", opp2)
second = enqueue_company(db, "Stripe", "CRO", opp2)
check("duplicate enqueue returns False", second is False)

with cursor(db) as cur:
    count = cur.execute(
        "SELECT COUNT(*) as n FROM enrichment_queue WHERE company_normalized='Stripe'"
    ).fetchone()["n"]
check("only one queue row for Stripe", count == 1, f"rows={count}")

# ── 10. Cache TTL skip ───────────────────────────────────────────────────────
section("10. Cache TTL — already-enriched company skips re-enqueue")
# Notion was enriched in test 8 — has_fresh_enrichment should be True
fresh = has_fresh_enrichment(db, "Notion")
check("has_fresh_enrichment=True for already-enriched company", fresh)
opp3 = seed_opp("Notion2")
skipped = enqueue_company(db, "Notion", "VP Sales", opp3)
check("enqueue_company skips fresh company", skipped is False)

# ── cleanup + summary ────────────────────────────────────────────────────────
try:
    os.unlink(db)
except Exception:
    pass

total = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed

print(f"\n{'='*40}")
print(f"Results: {passed}/{total} passed", "-- ALL GOOD" if failed == 0 else f"-- {failed} FAILED")
print("="*40)

sys.exit(0 if failed == 0 else 1)
