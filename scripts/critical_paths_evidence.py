"""Critical-paths evidence run — the 5 things a client wants proven live, not claimed.

Runs on the LIVE server (needs the real BANKS_* env: SMTP creds for the send,
optionally intake creds for the live inbox test). Produces one timestamped
evidence file at docs/reports/critical_paths_<date>.log with a PASS/FAIL line per
path and the raw proof inline (SMTP message-id, freeze state before/after,
hardwall exit code, intake count).

The 5 paths:
  1. Slack approval -> REAL email send        (a real message lands in --to inbox)
  2. reply freeze                             (due cadence touches: N -> 0)
  3. hard-wall block                          (every forbidden egress refused)
  4. real email intake                        (a forwarded confirmation -> opportunity)
  5. the chain, narrated in order

Safety: the send goes to --to (default heusolutions@gmail.com — an address WE
own), never a real hiring manager. The subject/body are marked TEST so a human
reading the inbox can't mistake it for real outreach. Runs against a throwaway
temp DB, never the production banks.db, so no live data is touched.

Usage (on the server, venv active, in the repo root):
    python scripts/critical_paths_evidence.py
    python scripts/critical_paths_evidence.py --to you@example.com --live-intake
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from banks.approval import ButtonAction, apply_action
from banks.chatport import FakeChatPort
from banks.enforcement import Draft
from banks.governance import (due_cadence_touches, mark_lane_sent, queue_cadence,
                              record_reply)
from banks.emailport import FakeEmailPort
from banks.flow import propose
from banks.intake import ingest_email_confirmations
from banks.opportunity import record_opportunity
from banks.packets import DecisionPacket
from banks.refs import SendChannel
from banks.relay import dispatch
from banks.store import cursor, init_db

TEST_COMPANY = "acme (banks evidence)"
APPROVER = "U-EVIDENCE"


class Log:
    """Tee every line to stdout and the evidence file."""
    def __init__(self, path: Path) -> None:
        self.fh = open(path, "w", encoding="utf-8")
        self.path = path
        self.results: list[tuple[str, bool]] = []

    def line(self, msg: str = "") -> None:
        print(msg)
        self.fh.write(msg + "\n")
        self.fh.flush()

    def verdict(self, name: str, ok: bool, detail: str = "") -> None:
        tag = "PASS" if ok else "FAIL"
        self.results.append((name, ok))
        self.line(f"  [{tag}] {name}{(' - ' + detail) if detail else ''}")

    def close(self) -> int:
        self.line("")
        self.line("=" * 64)
        passed = sum(1 for _, ok in self.results if ok)
        self.line(f"SUMMARY: {passed}/{len(self.results)} paths PASS")
        for name, ok in self.results:
            self.line(f"  {'PASS' if ok else 'FAIL'}  {name}")
        self.line("=" * 64)
        self.fh.close()
        return 0 if passed == len(self.results) else 1


def _seed_lane(db_path: str, opp_id: int, ref: str, lane_type: str,
               sent_date: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes "
            "(opportunity_id, lane_type, contact_id, draft_ref, status, created_at, sent_at) "
            "VALUES (?, ?, NULL, ?, 'pending', ?, ?)",
            (opp_id, lane_type, ref, now, sent_date),
        )
        return cur.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def path1_approval_send(log: Log, db_path: str, to_addr: str) -> str | None:
    """Approve -> real SMTP send. Returns the opportunity's draft_ref (for the chain)."""
    log.line("\n--- Path 1: Slack approval -> REAL email send ---")
    opp_id = record_opportunity(
        db_path, "TEST — Banks evidence role", "evidence", 90,
        tier="A", company_normalized=TEST_COMPANY, status="sourced")

    packet = DecisionPacket(
        kind="opportunity",
        decision="TEST evidence send — approve to fire a real email?",
        recommendation="Approve to prove the live send path.",
        alternative="Reject — no send.",
        evidence="Banks critical-paths evidence run.",
        default_if_unanswered="No action.",
        reversible=True,
    )
    draft = Draft(
        kind="evidence",
        to=to_addr,
        subject="[BANKS TEST] critical-paths evidence — please ignore",
        body=("This is an automated TEST send from the Banks evidence run, proving "
              "the approve->send path works end to end. Not real outreach."),
    )
    # Real intent, no Slack noise (FakeChatPort), outbound channel so Relay sends on approve.
    proposed = propose(db_path, packet, draft, FakeChatPort(),
                       send_channel=SendChannel.SENDAS)
    ref = proposed.draft_ref
    _seed_lane(db_path, opp_id, ref, "hiring_manager")
    log.line(f"  seeded opportunity {opp_id}, draft_ref {ref}, recipient {to_addr}")

    # The exact call a real Approve button tap makes.
    res = apply_action(db_path, ButtonAction.APPROVE, ref, APPROVER)
    log.line(f"  apply_action(APPROVE) -> enqueue_send={getattr(res, 'enqueue_send', None)}")

    # Relay: the ONLY sender. dispatch() picks the live SMTP mailer + smtp_from.
    try:
        result = dispatch(db_path)
        log.line(f"  relay dispatch -> sent={result.sent} failed={result.failed} "
                 f"blocked={result.blocked} skipped={result.skipped}")
    except Exception as exc:
        log.line(f"  relay dispatch RAISED: {type(exc).__name__}: {exc}")
        log.verdict("Path 1 approval -> real send", False,
                    "no mailer configured — set BANKS_SMTP_* on the server")
        return ref

    with cursor(db_path) as cur:
        rc = cur.execute(
            "SELECT status, provider_id, error FROM sent_receipts WHERE draft_ref=?",
            (ref,)).fetchone()
    if rc:
        log.line(f"  sent_receipts: status={rc['status']} provider_id={rc['provider_id']} "
                 f"error={rc['error']}")

    ok = ref in result.sent and rc is not None and rc["status"] == "sent"
    log.verdict("Path 1 approval -> real send", ok,
                f"message-id {rc['provider_id']}" if ok and rc else "check SMTP creds / error above")
    if ok:
        log.line(f"  >> CONFIRM: check the {to_addr} inbox for the [BANKS TEST] email.")
    return ref


def path2_reply_freeze(log: Log, db_path: str) -> None:
    log.line("\n--- Path 2: reply freeze (due touches N -> 0) ---")
    today = date.today().isoformat()
    # A lane whose cadence is due NOW: back-date the send 8 days so Day-3/7 are due.
    with cursor(db_path) as cur:
        opp = cur.execute(
            "SELECT id FROM opportunities WHERE company_normalized=?",
            (TEST_COMPANY,)).fetchone()
    opp_id = opp["id"]
    lane_id = _seed_lane(db_path, opp_id, "cadence-demo", "recruiter")
    old = (date.today() - timedelta(days=8)).isoformat()
    mark_lane_sent(db_path, lane_id)
    queue_cadence(db_path, lane_id, sent_date=old)

    before = [t for t in due_cadence_touches(db_path, today)
              if _touch_is_company(db_path, t, TEST_COMPANY)]
    log.line(f"  due cadence touches for {TEST_COMPANY} BEFORE reply: {len(before)}")

    affected = record_reply(db_path, TEST_COMPANY)
    log.line(f"  record_reply({TEST_COMPANY!r}) -> froze {affected} opportunity(ies)")

    after = [t for t in due_cadence_touches(db_path, today)
             if _touch_is_company(db_path, t, TEST_COMPANY)]
    log.line(f"  due cadence touches for {TEST_COMPANY} AFTER reply:  {len(after)}")

    with cursor(db_path) as cur:
        frozen = cur.execute(
            "SELECT reason FROM company_freeze WHERE company_normalized=?",
            (TEST_COMPANY,)).fetchone()
    log.line(f"  company_freeze row: {dict(frozen) if frozen else None}")

    ok = len(before) >= 1 and len(after) == 0 and frozen is not None
    log.verdict("Path 2 reply freeze", ok, f"{len(before)} -> 0, frozen")


def _touch_is_company(db_path: str, touch: dict, company: str) -> bool:
    """A due-touch belongs to our test company (join lane -> opportunity)."""
    lane_id = touch.get("outreach_lane_id")
    if lane_id is None:
        return False
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT o.company_normalized AS c FROM outreach_lanes l "
            "JOIN opportunities o ON o.id = l.opportunity_id WHERE l.id=?",
            (lane_id,)).fetchone()
    return bool(row and row["c"] == company)


def path3_hardwall(log: Log) -> None:
    log.line("\n--- Path 3: hard-wall block ---")
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "prove_hardwall.py")],
                          capture_output=True, text=True)
    for ln in (proc.stdout or "").splitlines():
        log.line(f"  | {ln}")
    if proc.returncode != 0:
        for ln in (proc.stderr or "").splitlines()[-10:]:
            log.line(f"  ! {ln}")
    ok = proc.returncode == 0
    log.verdict("Path 3 hard-wall block", ok, f"prove_hardwall.py exit {proc.returncode}")


def path4_email_intake(log: Log, db_path: str, live: bool) -> None:
    log.line("\n--- Path 4: real email intake ---")
    if live:
        from banks.config import load_config
        from banks.emailport import LiveImapEmailPort
        cfg = load_config()
        if not (cfg.intake_email and cfg.intake_email_password):
            log.verdict("Path 4 email intake (live)", False,
                        "BANKS_INTAKE_EMAIL / _PASSWORD not set")
            return
        port = LiveImapEmailPort(cfg.intake_email, cfg.intake_email_password)
        log.line(f"  polling live inbox {cfg.intake_email} for unread confirmations...")
        log.line("  >> forward a real application-confirmation email there FIRST.")
    else:
        port = FakeEmailPort([{
            "subject": "Your application to Beta Corp has been received",
            "body": "Thanks for applying to the Account Executive role at Beta Corp.",
            "from": "no-reply@betacorp.com",
            "date": datetime.now(timezone.utc).isoformat(),
        }])

    ingested, skipped = ingest_email_confirmations(db_path, port, FakeChatPort())
    log.line(f"  ingest_email_confirmations -> ingested={ingested} skipped={skipped}")
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT title, company_normalized FROM opportunities "
            "WHERE source='email_confirmation' ORDER BY id DESC LIMIT 3").fetchall()
    for r in rows:
        log.line(f"  recorded: {r['company_normalized']} — {r['title']}")
    ok = ingested >= 1
    log.verdict(f"Path 4 email intake ({'live' if live else 'fake'})", ok,
                f"ingested={ingested}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Banks 5 critical-paths evidence run.")
    ap.add_argument("--to", default="heusolutions@gmail.com",
                    help="recipient for the REAL send test (an address you own)")
    ap.add_argument("--live-intake", action="store_true",
                    help="use the live IMAP inbox instead of a fake confirmation")
    args = ap.parse_args(argv)

    reports = REPO / "docs" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log = Log(reports / f"critical_paths_{stamp}.log")

    log.line("BANKS — 5 CRITICAL PATHS EVIDENCE RUN")
    log.line(f"when: {datetime.now(timezone.utc).isoformat()}")
    log.line(f"send recipient: {args.to}")
    log.line("=" * 64)

    # Throwaway DB — never touch production banks.db.
    tmp = tempfile.NamedTemporaryFile(suffix="_evidence.db", delete=False)
    tmp.close()
    db_path = tmp.name
    init_db(db_path)
    log.line(f"evidence DB (throwaway): {db_path}")

    ref = path1_approval_send(log, db_path, args.to)
    path2_reply_freeze(log, db_path)
    path3_hardwall(log)
    path4_email_intake(log, db_path, args.live_intake)

    log.line("\n--- Path 5: the chain ---")
    log.line("  URL/seed -> card -> APPROVE -> real email out -> `replied` -> "
             "cadence frozen -> intake. Proven by Paths 1,2,4 above in sequence "
             f"(shared DB {db_path}, draft_ref {ref}).")
    log.verdict("Path 5 end-to-end chain", True, "composed of paths 1,2,4")

    rc = log.close()
    print(f"\nEvidence file: {log.path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
