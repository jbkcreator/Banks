"""MOD-05 live smoke test — posts a real Daily Attack Queue to the TEST Slack
workspace (Lesly's "bank test"). Seeds a realistic day into a throwaway DB, then
calls post_daily_queue against the LIVE ChatPort.

Run from repo root:  python scripts/mod05_smoke_test.py
Needs BANKS_SLACK_BOT_TOKEN + BANKS_CHANNEL_ID in .env (test workspace).
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone, timedelta

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k] = v.split("#", 1)[0].strip()

# Use the SAME db the socket listener reads (banks_live.db) so button clicks
# find these drafts' send-intents. Fresh each run.
db = str(root / "banks_live.db")
if os.path.exists(db):
    os.remove(db)
os.environ["BANKS_DB_PATH"] = db

from banks.store import init_db, cursor
from banks.opportunity import record_opportunity, CareerFacts
from banks.attack_queue import build_sections, post_daily_queue
from banks.chatport import LiveChatPort

init_db(db)
now = datetime.now(timezone.utc)
now_iso = now.isoformat()

FACTS = CareerFacts(
    identity="GTM leader, 15 yrs building enterprise sales orgs",
    experience=("VP Sales at a PropTech scale-up", "Director of GTM at a SaaS company"),
    skills=("enterprise sales", "GTM strategy", "team building"),
    seeking="VP Sales / CRO roles in PropTech or SaaS",
)


def seed():
    with cursor(db) as cur:
        # A warm contact at the Tier A company (hiring-manager lane target)
        cur.execute(
            "INSERT INTO contacts (name, company, email, linkedin_url, degree, source, "
            "verified, added_at) VALUES "
            "('Camryn Hare','secondnature','camryn@secondnature.com',"
            "'https://linkedin.com/in/camryn',1,'linkedin_csv',1,?)",
            (now_iso,),
        )
        cid = cur.lastrowid
        # A relationship contact untouched 14+ days (Network Activation Lite)
        cur.execute(
            "INSERT INTO contacts (name, company, title, degree, source, added_at) VALUES "
            "('Priya Nair','appfolio','VP Marketing',1,'alumni_csv',?)",
            ((now - timedelta(days=40)).isoformat(),),
        )

    # Tier A opportunity with two pending lanes + send intents (so cards render)
    opp_a = record_opportunity(
        db, "Head of Onboarding", "simplify", 88,
        tier="A", company_normalized="secondnature", industry="PropTech", contact_id=cid,
    )
    # Tier B + Tier C for the imported digest
    record_opportunity(db, "Sr AE", "simplify", 61, tier="B", company_normalized="ketch")
    record_opportunity(db, "SDR", "simplify", 40, tier="C", company_normalized="acme")

    with cursor(db) as cur:
        for lane, subj, body, to in [
            ("hiring_manager", "① CLICK APPROVE → Interest in Head of Onboarding",
             "Hi Camryn, I came across the Head of Onboarding role and wanted to reach "
             "out directly. My background: VP Sales at a PropTech scale-up.\n"
             "[Draft from career-facts only — review before sending.]",
             "camryn@secondnature.com"),
            ("recruiter", "② CLICK MARK SENT → Keep me on file, GTM mandates",
             "Hi, wanted to stay on your radar for GTM mandates.\n"
             "[Draft from career-facts only — review before sending.]", "secondnature"),
            ("employee", "③ CLICK REJECT → Question about Second Nature",
             "Hi, I'm exploring the Head of Onboarding role and would love your take on "
             "the company.\n[Draft from career-facts only — review before sending.]", "someone"),
            ("hiring_manager", "④ REPLY 'shorter' IN THREAD → Follow-up to hiring manager",
             "Hi Camryn, following up on my earlier note about the Head of Onboarding role. "
             "I remain very interested and would welcome the chance to talk through how my "
             "GTM background maps to what your team needs this quarter.\n"
             "[Draft from career-facts only — review before sending.]", "camryn@secondnature.com"),
        ]:
            # A real decision_packet — its integer id IS the draft_ref (DraftRef
            # = str(packet_id)), so button clicks parse and apply_action works.
            cur.execute(
                "INSERT INTO decision_packets (kind, decision, recommendation, "
                "default_if_unanswered, reversible, created_at) "
                "VALUES (?, ?, 'Review and approve to send', 'skip', 1, ?)",
                (f"outreach_{lane}", subj, now_iso),
            )
            ref = str(cur.lastrowid)
            cur.execute(
                "INSERT INTO outreach_lanes (opportunity_id, lane_type, contact_id, "
                "draft_ref, status, created_at) VALUES (?,?,?,?, 'pending', ?)",
                (opp_a, lane, cid if lane == "hiring_manager" else None, ref, now_iso),
            )
            cur.execute(
                "INSERT INTO send_intents (draft_ref, send_channel, to_addr, subject, body, "
                "status, created_at) VALUES (?, 'email:sendas', ?, ?, ?, 'pending', ?)",
                (ref, to, subj, body, now_iso),
            )
        # A funnel event so the footer renders
        cur.execute(
            "INSERT INTO funnel_events (opportunity_id, event_type, ts) VALUES (?, 'applied', ?)",
            (opp_a, now_iso),
        )


seed()

print("=== build_sections (dry preview) ===")
for s in build_sections(db, now=now, career_facts=FACTS):
    print(f"  [{s.category}] {s.title}")
    for ln in s.lines:
        print(f"      · {ln}")
    for c in s.cards:
        print(f"      ▸ card: {c['kind']} — {c['subject']}")

print("\n=== posting LIVE to test workspace ===")
chat = LiveChatPort()
res = post_daily_queue(db, chat, now=now, career_facts=FACTS)
print("result:", res)

print("\n=== idempotency: second call same day ===")
res2 = post_daily_queue(db, chat, now=now, career_facts=FACTS)
print("result:", res2, "(skipped should be True — no duplicate post)")
