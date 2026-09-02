"""Job dispatch — turns a due StandingJob into a real posted action (#4).

scheduler.py is the clock (which jobs are due); this is the hands. A tick calls
run_due_jobs(now, db, chat): for each due job it performs the side effect —
morning_dashboard posts the B-D1 brief to #banks. Other jobs are wired as their
engines land. Keeps the clock pure and testable, the effects here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .activity_log import hours_saved_this_week, log_event
from .briefing import render_brief_blocks
from .chatport import ChatPort
from .halt import check_halt
from .schedule import OpportunityCostInputs, roi_line, weekly_roi
from .scheduler import due_jobs
from .scorecard import render_weekly_scorecard, count_reds


def _weekly_scorecard_blocks(db_path: str) -> list[dict]:
    from datetime import date, timedelta
    today = date.today()
    # Most recent Friday (or today if Friday).
    days_back = (today.weekday() - 4) % 7
    week_ending = (today - timedelta(days=days_back)).isoformat()
    lines = render_weekly_scorecard(db_path, week_ending)
    hrs = hours_saved_this_week(db_path)
    reds = count_reds(lines)
    emoji = "🔴" if reds >= 3 else ("🟡" if reds >= 1 else "🟢")
    header = f"{emoji} Weekly Scorecard — {week_ending}"
    body_lines = [
        f"{'🔴' if ln.red else '🟢'} {ln.label}: {ln.value} (target {ln.target})"
        for ln in lines
    ]
    roi = weekly_roi(OpportunityCostInputs(hours_saved=hrs))
    body_lines.append(f"⏱ {roi_line(roi)}")
    return [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(body_lines)},
        },
    ]


def _already_ran_today(db_path: str, job_name: str) -> bool:
    """Return True if job_name has a successful run recorded for today (UTC)."""
    from .store import cursor
    today = datetime.now(timezone.utc).date().isoformat()
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT 1 FROM job_runs WHERE job_name=? AND status='ok' "
            "AND substr(started_at,1,10)=? LIMIT 1",
            (job_name, today),
        ).fetchone()
    return row is not None


def run_job(name: str, db_path: str, chat: ChatPort) -> dict | None:
    """Perform one named job. Returns the post result, or None if no effect yet."""
    check_halt()  # T3-14: every job checks the halt flag before doing any work
    if name == "morning_dashboard":
        if _already_ran_today(db_path, "morning_dashboard"):
            return {"skipped": True}
        log_event(db_path, "draft_created", meta={"job": "morning_dashboard"}, minutes_saved=0)
        return chat.post_blocks("Banks — Morning Brief", render_brief_blocks(db_path))
    if name == "weekly_scorecard":
        if _already_ran_today(db_path, "weekly_scorecard"):
            return {"skipped": True}
        log_event(db_path, "scorecard_posted", meta={"job": "weekly_scorecard"})
        return chat.post_blocks("Banks — Weekly Scorecard", _weekly_scorecard_blocks(db_path))
    if name == "daily_attack_queue":
        # MOD-05 cockpit. Idempotent per date (post_daily_queue claims the day),
        # so the self-heal retry wrapper can re-fire without double-posting.
        from .attack_queue import post_daily_queue
        from .opportunity import load_career_facts
        log_event(db_path, "draft_created", meta={"job": "daily_attack_queue"}, minutes_saved=0)
        return post_daily_queue(db_path, chat, career_facts=load_career_facts())
    if name == "nightly_reflection":
        from .reflection import run_reflection
        return run_reflection(db_path, chat)
    if name == "relay_dispatch":
        # An Approve only flips send_intents to 'approved' (approval.py) —
        # Relay is the sole credential-holder that actually sends (R-D1),
        # so the mailer selection lives in relay.dispatch, not here.
        from .relay import dispatch as relay_dispatch
        try:
            result = relay_dispatch(db_path)
        except RuntimeError:
            return None  # no mailer configured yet; retry on the next tick
        if result.sent:
            print(f"[relay] dispatched {len(result.sent)} send(s): "
                  f"{result.sent} (blocked={result.blocked} failed={result.failed})",
                  flush=True)
            log_event(db_path, "draft_created", meta={"job": "relay_dispatch",
                      "sent": len(result.sent)}, minutes_saved=0)
        return {"sent": result.sent, "skipped": result.skipped,
                "failed": result.failed, "blocked": result.blocked}
    if name == "email_intake_poll":
        # MOD-01: poll the forwarded-confirmation mailbox over IMAP and record
        # new opportunities. No-op unless the intake creds are set, so an
        # unprovisioned Banks skips rather than erroring on every tick.
        from .config import load_config
        from .emailport import LiveImapEmailPort
        from .intake import ingest_email_confirmations
        cfg = load_config()
        if not (cfg.intake_email and cfg.intake_email_password):
            return None
        port = LiveImapEmailPort(cfg.intake_email, cfg.intake_email_password)
        from .llmport import load_llm_port
        ingested, skipped = ingest_email_confirmations(
            db_path, port, chat, load_llm_port())
        if ingested:
            log_event(db_path, "draft_created", meta={"job": "email_intake_poll",
                      "ingested": ingested}, minutes_saved=0)
        return {"ingested": ingested, "skipped": skipped} if (ingested or skipped) else None
    if name in ("enrichment_submit", "enrichment_retrieve"):
        # MOD-02: Clay enrichment (webhook push + Sheet pull). Both no-op unless
        # the paid creds (webhook URL + Sheet ID) are set, so an unprovisioned
        # Banks just skips rather than erroring on every tick.
        from .config import load_config
        from .contact_enrichment import (drain_submitted, select_enrichment_port,
                                          submit_pending)
        port = select_enrichment_port(load_config())
        if port is None:
            return None
        if name == "enrichment_submit":
            batch_id = submit_pending(db_path, port)
            return {"submitted_batch": batch_id} if batch_id else None
        written = drain_submitted(db_path, port)
        if written:
            log_event(db_path, "draft_created", meta={"job": "enrichment_retrieve",
                      "contacts": written}, minutes_saved=0)
        return {"enriched_contacts": written} if written else None
    return None


def run_due_jobs(now: datetime, db_path: str, chat: ChatPort,
                 timezone_name: str = "America/New_York") -> list[str]:
    """Fire every job due at `now`. Returns the names actually run."""
    ran = []
    for job in due_jobs(now, timezone_name):
        if run_job(job.name, db_path, chat) is not None:
            ran.append(job.name)
    return ran
