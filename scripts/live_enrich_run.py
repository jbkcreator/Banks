import os, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ[k] = v
DB = os.environ.get("BANKS_DB_PATH") or str(root / "banks_live.db")
os.environ["BANKS_DB_PATH"] = DB

DL = pathlib.Path(r"C:\Users\Lesly\Downloads")
from banks.store import init_db, cursor
from banks.csvport import (LiveCSVPort, parse_linkedin_connection_row,
                           parse_alumni_row, parse_recruiter_row)
from banks.chatport import LiveChatPort
from banks.llmport import load_llm_port
from banks.exclusion import add_company_exclusion
from banks.intake import ingest_contacts, ingest_simplify
from banks.enrich import LiveFetchPort, enrich_pending

init_db(DB)
add_company_exclusion(DB, "Rent Solutions", "client exclusion")
cp = LiveCSVPort()
ingest_contacts(DB, cp, str(DL / "Connections_1_6492.csv"), parse_linkedin_connection_row, skip_until_header="First Name")
ingest_contacts(DB, cp, str(DL / "Banks_Alumni_FormerColleagues_2_9671.csv"), parse_alumni_row)
ingest_contacts(DB, cp, str(DL / "Banks_Recruiter_Registry_2_7484.csv"), parse_recruiter_row)

res = ingest_simplify(DB, cp, str(DL / "Simplify_Tracked_Jobs_2026-08-24_2_1743.csv"), LiveChatPort())
print("SIMPLIFY: ingested", res.ingested, "held", res.held, "surfaced", res.surfaced)

print("Enriching first 5 held postings (real fetch + real LLM + real Slack)...")
batch = enrich_pending(DB, LiveFetchPort(), load_llm_port(), LiveChatPort(), limit=5)
print("ENRICH: processed", batch.processed, "surfaced", batch.surfaced,
      "still_held", batch.still_held, "fetch_failed", batch.fetch_failed)
with cursor(DB) as c:
    for r in batch.results:
        row = c.execute("SELECT title, company_normalized FROM opportunities WHERE id=?", (r.opportunity_id,)).fetchone()
        print(f"  {row['title']} @ {row['company_normalized']} -> {r.outcome}, tier {r.tier}, fit {r.fit}")
