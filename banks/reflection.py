"""Nightly reflection job (#11) — daily recap posted to #banks at 23:00 ET.

Reads activity_log for the day, tallies actions taken, hours saved,
drafts approved/sent, and any open items aging past threshold.
Posts a single concise Block Kit message — not a report, a quick scan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .activity_log import log_event, recent_events
from .chatport import ChatPort
from .store import cursor


def reflection_sections(db_path: str, now: datetime | None = None) -> list[tuple[str, list[str]]]:
    """Pure/testable: returns (title, lines) pairs for today's reflection."""
    now = now or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Events today.
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT kind, COUNT(*) as n, SUM(minutes_saved) as mins "
            "FROM activity_log WHERE ts >= ? GROUP BY kind ORDER BY n DESC",
            (day_start.isoformat(),),
        )
        rows = [dict(r) for r in cur.fetchall()]

    total_mins = sum((r["mins"] or 0) for r in rows)
    total_events = sum(r["n"] for r in rows)

    sections: list[tuple[str, list[str]]] = []

    # Summary line.
    sections.append((
        f"Today ({now.date().isoformat()})",
        [f"{total_events} actions · {total_mins/60:.1f}h saved"]
        if rows else ["No events logged today."],
    ))

    if rows:
        breakdown = [f"• {r['kind']}: {r['n']} ({(r['mins'] or 0)/60:.1f}h)" for r in rows[:6]]
        sections.append(("Breakdown", breakdown))

    # Aging approved-but-unsent.
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM send_intents "
            "WHERE status = 'approved' AND created_at <= ?",
            ((now - timedelta(hours=12)).isoformat(),),
        )
        aging_n = cur.fetchone()["n"]
    if aging_n:
        sections.append(("Aging items", [f"⚠️ {aging_n} approved but not sent (>12h)"]))

    # Open decisions count.
    with cursor(db_path) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM decision_packets WHERE answered_at IS NULL")
        open_decisions = cur.fetchone()["n"]
    sections.append(("Open decisions", [f"{open_decisions} pending your answer"]))

    return sections


def render_reflection_blocks(db_path: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Banks — Nightly Recap"}}
    ]
    for title, lines in reflection_sections(db_path, now):
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*\n" + "\n".join(lines)},
        })
    return blocks


def run_reflection(db_path: str, chat: ChatPort, now: datetime | None = None) -> dict:
    """Post nightly reflection to #banks. Called by jobs.run_job('nightly_reflection')."""
    now = now or datetime.now(timezone.utc)
    blocks = render_reflection_blocks(db_path, now)
    log_event(db_path, "reflection_posted", meta={"date": now.date().isoformat()})
    return chat.post_blocks("Banks — Nightly Recap", blocks)


# ---------------------------------------------------------------------------
# Amendment proposals (T2-11)
#
# AMENDABLE sections are those the constitution marks as runtime-adjustable
# without requiring a full constitution revision. Banks may propose one-line
# diffs to these sections only — never self-installs, always surfaces for
# approve/reject.

AMENDABLE_SECTIONS: tuple[str, ...] = (
    "standing_jobs",        # job schedules / triggers
    "brief_cadence",        # brief timing / sections
    "scorecard_targets",    # numeric thresholds
    "contact_windows",      # touch-window durations
)


def propose_amendment(db_path: str, section: str, current_text: str,
                      proposed_text: str, rationale: str, chat: ChatPort) -> dict:
    """Surface a one-line diff against an AMENDABLE section for Josh's approve/reject.

    Never self-installs. Posts a Block Kit message with Approve/Reject buttons
    and returns the post result. The amendment is a draft — it cannot take
    effect until Josh explicitly approves it.
    """
    if section not in AMENDABLE_SECTIONS:
        raise ValueError(
            f"section {section!r} is not AMENDABLE — Banks may only propose diffs "
            f"to: {AMENDABLE_SECTIONS}"
        )
    from .enforcement import Draft, sign
    from .flow import propose
    from .refs import SendChannel

    body = sign(
        f"Amendment proposal for `{section}`:\n\n"
        f"**Current:** {current_text}\n"
        f"**Proposed:** {proposed_text}\n\n"
        f"Rationale: {rationale}\n\n"
        f"_This takes effect only on your explicit Approve. Banks never self-installs._"
    )
    draft = Draft(
        kind="amendment_proposal",
        to="you",
        subject=f"Amendment proposal — {section}",
        body=body,
    )
    from .packets import DecisionPacket
    packet = DecisionPacket(
        kind="amendment_proposal",
        decision=f"Approve amendment to {section}?",
        recommendation="Review diff and decide",
        default_if_unanswered="reject",
        evidence=f"section={section}",
    )
    return propose(db_path, packet, draft, chat, send_channel=SendChannel.INTERNAL)
