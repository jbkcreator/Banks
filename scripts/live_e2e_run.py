"""Live end-to-end run against the CLIENT Slack — no human click required.

Proves the actual flow reaches the client channel and completes, substituting a
direct apply_action() call for the human button tap (a click just calls that
function). The send is stubbed with FakeMailer so no real outreach leaves the
box — the drafts-only wall stays intact.

Flow exercised:
    seed Tier-A opportunity + verified hiring-manager contact
      -> generate_surround_pack  (REAL cards posted to the client #banks channel)
      -> SIMULATE approve: apply_action(APPROVE, draft_ref, Josh's user id)
      -> relay_run(FakeMailer)   (would send; stubbed)
      -> verify DB: intent 'sent', receipt, cadence queued, funnel event

Reads CLIENT_SLACK_* from .env and maps them onto the BANKS_* names for this
process only — exactly the cutover swap, so this doubles as a cutover rehearsal.

Run:  python scripts/live_e2e_run.py
      python scripts/live_e2e_run.py --keep    # leave the posted cards in-channel
Exit 0 = full flow green.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

# --- load .env, then map CLIENT_SLACK_* -> BANKS_* (cutover for this run) ----
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.split("#", 1)[0].strip())

_CUTOVER = {
    "CLIENT_SLACK_BOT_TOKEN": "BANKS_SLACK_BOT_TOKEN",
    "CLIENT_SLACK_APP_TOKEN": "BANKS_SLACK_APP_TOKEN",
    "CLIENT_SLACK_CHANNEL_ID": "BANKS_CHANNEL_ID",
    "CLIENT_SLACK_JOBS_CHANNEL_ID": "BANKS_JOBS_CHANNEL_ID",
    "CLIENT_SLACK_APPROVER_USER_ID": "BANKS_APPROVER_USER_ID",
}
for src, dst in _CUTOVER.items():
    if os.environ.get(src):
        os.environ[dst] = os.environ[src]

APPROVER = os.environ.get("BANKS_APPROVER_USER_ID", "U_UNKNOWN")
KEEP = "--keep" in sys.argv

if not os.environ.get("BANKS_SLACK_BOT_TOKEN"):
    print("[STOP] CLIENT_SLACK_BOT_TOKEN not in .env — run slack_healthcheck first.")
    sys.exit(1)

# fresh throwaway DB
db = str(pathlib.Path(tempfile.mkdtemp()) / "e2e.db")
os.environ["BANKS_DB_PATH"] = db

from banks.approval import ButtonAction, apply_action           # noqa: E402
from banks.chatport import LiveChatPort                          # noqa: E402
from banks.mailer import FakeMailer                              # noqa: E402
from banks.opportunity import load_career_facts, record_opportunity  # noqa: E402
from banks.relay import relay_run                                # noqa: E402
from banks.store import cursor, init_db                          # noqa: E402
from banks.surround import generate_surround_pack               # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    return ok


def _verified_contact(db_path, name, company, email) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO contacts (name, company, email, linkedin_url, degree, "
            "source, verified, added_at) VALUES (?,?,?,?,1,'clay_enrichment',1,?)",
            (name, company, email, "", now))
        return cur.lastrowid


def main() -> int:
    init_db(db)
    facts = load_career_facts()
    check("career-facts present", not facts.is_empty(),
          "loaded from career-facts.md" if not facts.is_empty()
          else "EMPTY — outreach cannot draft")
    if facts.is_empty():
        return _summary()

    chat = LiveChatPort()

    # --- seed a clean Tier-A opportunity with a verified hiring-manager -------
    cid = _verified_contact(db, "Alex Rivera", "secondnature", "alex@secondnature.example")
    opp = record_opportunity(
        db, "VP Revenue", "simplify", 88, tier="A",
        company_normalized="secondnature", industry="PropTech", contact_id=cid)

    # --- surround: posts REAL cards to the client channel --------------------
    try:
        pack = generate_surround_pack(db, opp, facts, chat)
        posted = check("surround posts to client Slack", len(pack.lanes) > 0,
                       f"{len(pack.lanes)} lane card(s) posted to #banks")
    except Exception as e:
        return _fail_out("surround posts to client Slack", e)
    if not posted:
        return _summary()

    hm = next((l for l in pack.lanes if l["type"] == "hiring_manager"), None)
    check("outbound (hiring-manager) lane present", hm is not None,
          "verified email -> email:sendas intent" if hm else "no outbound lane produced")
    if hm is None:
        return _summary()

    ref = hm["draft_ref"]

    # --- SIMULATE Josh's approval (this is what the button click calls) -------
    apply_action(db, ButtonAction.APPROVE, ref, APPROVER)
    with cursor(db) as cur:
        st = cur.execute("SELECT status FROM send_intents WHERE draft_ref=?",
                         (ref,)).fetchone()
    check("approve flips intent to 'approved'", st and st["status"] == "approved",
          f"intent status={st['status'] if st else 'MISSING'} (approver {APPROVER})")

    # --- relay: would send; stubbed so no real email leaves the box ----------
    mailer = FakeMailer()
    res = relay_run(db, mailer)
    check("relay sends the approved draft (stubbed)", ref in res.sent,
          f"sent={len(res.sent)} blocked={len(res.blocked)} failed={len(res.failed)}")
    check("mailer received exactly the approved payload", len(mailer.sent) == 1,
          f"{len(mailer.sent)} message(s) captured (no real send)")

    # --- verify downstream state transitions ---------------------------------
    with cursor(db) as cur:
        rec = cur.execute("SELECT status FROM sent_receipts WHERE draft_ref=?",
                          (ref,)).fetchone()
        cad = cur.execute(
            "SELECT COUNT(*) n FROM cadence_queue cq JOIN outreach_lanes ol "
            "ON ol.id=cq.outreach_lane_id WHERE ol.draft_ref=?", (ref,)).fetchone()
        fun = cur.execute(
            "SELECT COUNT(*) n FROM funnel_events WHERE event_type='outreach_sent'"
        ).fetchone()
    check("sent_receipt recorded", rec and rec["status"] == "sent",
          f"receipt={rec['status'] if rec else 'MISSING'}")
    check("Day 3/7/14 cadence queued", cad and cad["n"] == 3, f"{cad['n']} touches")
    check("funnel event logged", fun and fun["n"] >= 1, f"{fun['n']} outreach_sent")

    return _summary()


def _fail_out(name, e) -> int:
    check(name, False, f"{type(e).__name__}: {e}")
    return _summary()


def _summary() -> int:
    print("\n" + "=" * 68)
    print("  BANKS - LIVE END-TO-END RUN (client Slack, no human click)")
    print("=" * 68)
    fails = 0
    for name, ok, detail in results:
        if not ok:
            fails += 1
        safe = detail.encode("ascii", "replace").decode("ascii")
        print(f"  {'[PASS]' if ok else '[FAIL]'} {name:40} {safe}")
    print("=" * 68)
    if fails == 0:
        print("  FULL FLOW GREEN - intake -> surface -> approve -> send, end to end.")
        print("  Real cards posted to the client channel; send stubbed (no email sent).")
        if not KEEP:
            print("  (Posted cards left in the client channel - delete them manually.)")
    else:
        print(f"  {fails} STEP(S) FAILED - see [FAIL] rows above.")
    print("=" * 68)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
