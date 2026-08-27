"""MOD-05 Daily Attack Queue — the morning job-search cockpit.

A VIEW + ROUTING layer over the MOD-01/03/04 pipeline: it renders what needs
attention and tracks its own view-state (snooze/skip/aging), but never
recomputes pipeline state. Mirrors briefing.py's split — pure `build_sections`
+ a thin render/post layer over ChatPort.

Layout (Q2/Q20): one summary header post + individually-approvable cards
threaded beneath it, fresh per day, exactly-once via the `daily_queue` claim.
Order is failure-mode-first (Q25); empty sections are omitted (Q14).

Production note: the live jobs channel is BANKS_JOBS_CHANNEL_ID — wire a
ChatPort whose channel is that. Slack threads one level deep, so revision
targeting is by the card's own ts (queue_items.card_ts), see revisions.py.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime as dt, timezone
from typing import TYPE_CHECKING

from .approval import render_draft_blocks
from .enforcement import Draft
from .governance import (
    due_cadence_touches,
    network_activation_due,
    weekly_funnel_summary,
)
from .store import cursor, transaction

if TYPE_CHECKING:
    from .chatport import ChatPort
    from .opportunity import CareerFacts


@dataclass
class Section:
    """One queue section. `lines` are informational; `cards` are approvable drafts."""

    title: str
    lines: list[str] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    category: str = ""


# ---------------------------------------------------------------------------
# Pure view assembly (no I/O beyond DB reads) — fully unit-testable
# ---------------------------------------------------------------------------

def build_sections(
    db_path: str,
    now: dt | None = None,
    career_facts: "CareerFacts | None" = None,
) -> list[Section]:
    """Assemble the day's sections, failure-mode-first, empties omitted.

    career_facts empty/None → Tier A/B show a single "fill career-facts" blocker
    line instead of cards (Q27), rather than crashing.
    """
    now = now or dt.now(timezone.utc)
    today = now.date().isoformat()

    carried = _carried_over_cards(db_path, today)
    carried_refs = {c["draft_ref"] for c in carried}

    facts_empty = career_facts is None or career_facts.is_empty()

    out: list[Section] = []

    if carried:
        out.append(Section(
            "⚠️ Carried over",
            [f"{len(carried)} item(s) still awaiting you — oldest first"],
            carried,
            category="carried_over",
        ))

    active = _active_conversation_lines(db_path, now)
    if active:
        out.append(Section("🔥 Active conversations", active, [], category="active_convo"))

    for tier, label in (("A", "🎯 Tier A surround"), ("B", "🎯 Tier B")):
        if facts_empty:
            blocker = _tier_blocker_line(db_path, tier)
            if blocker:
                out.append(Section(label, [blocker], [], category=f"tier_{tier.lower()}"))
        else:
            cards = _tier_lane_cards(db_path, tier, carried_refs)
            if cards:
                out.append(Section(label, [], cards, category=f"tier_{tier.lower()}"))

    follow_cards, follow_lines = _follow_up_items(db_path, today, carried_refs)
    if follow_cards or follow_lines:
        out.append(Section("📨 Follow-ups due", follow_lines, follow_cards, category="follow_up"))

    relationship = _relationship_lines(db_path, today)
    if relationship:
        out.append(Section("🤝 Relationship outreach", relationship, [], category="relationship"))

    imported = _imported_digest_line(db_path)
    if imported:
        out.append(Section("📥 Imported", [imported], [], category="imported"))

    funnel = _funnel_footer_line(db_path, today)
    if funnel:
        out.append(Section("📊 Funnel (7d)", [funnel], [], category="funnel"))

    if not out:
        out.append(Section(
            "🎯 Daily Attack Queue",
            ["Nothing queued today. Add a job with `applied at <company>` or paste a JD."],
            [],
            category="empty",
        ))
    return out


def _carried_over_cards(db_path: str, today: str) -> list[dict]:
    """Pending items first surfaced on an earlier day — the dropped balls.

    Includes snoozed items whose snooze_until has arrived (they re-enter here).
    """
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT qi.draft_ref, qi.first_surfaced_at, qi.category, "
            "       si.subject, si.body, si.to_addr "
            "FROM queue_items qi "
            "JOIN send_intents si ON si.draft_ref = qi.draft_ref "
            "WHERE (qi.state = 'active' "
            "       OR (qi.state = 'snoozed' AND qi.snooze_until <= ?)) "
            "  AND si.status = 'pending' "
            "  AND substr(qi.first_surfaced_at, 1, 10) < ? "
            "ORDER BY qi.first_surfaced_at",
            (today, today),
        ).fetchall()
    cards = []
    for r in rows:
        age = _age_days(r["first_surfaced_at"], today)
        cards.append({
            "draft_ref": r["draft_ref"],
            "kind": r["category"] or "carried_over",
            "subject": f"⏳ {r['subject'] or 'draft'} (aging {age}d)",
            "body": r["body"] or "",
            "to": r["to_addr"] or "",
        })
    return cards


def _active_conversation_lines(db_path: str, now: dt) -> list[str]:
    """Frozen companies (a live reply — MOD-04 got_reply). No inbox reading."""
    now_iso = now.isoformat()
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT company_normalized, frozen_at, thaw_at FROM company_freeze"
        ).fetchall()
    lines = []
    for r in rows:
        thaw = r["thaw_at"]
        if thaw is None or now_iso < thaw:
            lines.append(
                f"🔥 {r['company_normalized']} — in conversation "
                f"(since {(r['frozen_at'] or '')[:10]}); other outreach frozen"
            )
    return lines


def _tier_lane_cards(db_path: str, tier: str, exclude_refs: set[str]) -> list[dict]:
    """Pending outreach lanes for opportunities of this tier, score-ranked."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT ol.draft_ref, ol.lane_type, o.title, o.criteria_match_score, "
            "       si.subject, si.body, si.to_addr "
            "FROM outreach_lanes ol "
            "JOIN opportunities o ON o.id = ol.opportunity_id "
            "JOIN send_intents si ON si.draft_ref = ol.draft_ref "
            "WHERE o.tier = ? AND ol.status = 'pending' AND si.status = 'pending' "
            "ORDER BY o.criteria_match_score DESC, ol.id",
            (tier,),
        ).fetchall()
    cards = []
    for r in rows:
        if r["draft_ref"] in exclude_refs:
            continue
        cards.append({
            "draft_ref": r["draft_ref"],
            "kind": r["lane_type"],
            "subject": r["subject"] or r["title"],
            "body": r["body"] or "",
            "to": r["to_addr"] or "",
        })
    return cards


def _tier_blocker_line(db_path: str, tier: str) -> str | None:
    """career-facts empty → surface the count + the unlock nudge (Q27)."""
    with cursor(db_path) as cur:
        n = cur.execute(
            "SELECT COUNT(*) n FROM opportunities WHERE tier = ? AND needs_enrichment = 0",
            (tier,),
        ).fetchone()["n"]
    if not n:
        return None
    noun = "opportunity" if n == 1 else "opportunities"
    return (
        f"{n} Tier {tier} {noun} ready — ⚠️ blocked: career-facts.md is empty. "
        f"Reply with your resume to unlock drafts."
    )


def _follow_up_items(
    db_path: str, today: str, exclude_refs: set[str]
) -> tuple[list[dict], list[str]]:
    """Due cadence touches → cards if a pending draft exists, else info lines."""
    due = due_cadence_touches(db_path, today)
    cards, lines = [], []
    with cursor(db_path) as cur:
        for t in due:
            ref = t.get("draft_ref")
            si = None
            if ref:
                si = cur.execute(
                    "SELECT subject, body, to_addr, status FROM send_intents WHERE draft_ref = ?",
                    (ref,),
                ).fetchone()
            if ref and ref not in exclude_refs and si and si["status"] == "pending":
                cards.append({
                    "draft_ref": ref,
                    "kind": "follow_up",
                    "subject": si["subject"] or f"Follow-up #{t['touch_number']}",
                    "body": si["body"] or "",
                    "to": si["to_addr"] or "",
                })
            else:
                lines.append(
                    f"• Follow-up #{t['touch_number']} due — {t['lane_type']} "
                    f"(opportunity {t['opportunity_id']})"
                )
    return cards, lines


def _relationship_lines(db_path: str, today: str) -> list[str]:
    """Network Activation Lite — the same engine as the on-demand call list."""
    contacts = network_activation_due(db_path, today, limit=3)
    lines = []
    for c in contacts:
        role = c.get("title") or c.get("position") or ""
        name = c.get("name") or "contact"
        suffix = f" ({role})" if role else ""
        lines.append(f"• {name}{suffix} — reach out (untouched 14+ days)")
    return lines


def _imported_digest_line(db_path: str) -> str | None:
    """Informational receipt — counts by tier + enrichment-held (Q17)."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT tier, COUNT(*) n FROM opportunities GROUP BY tier"
        ).fetchall()
        held = cur.execute(
            "SELECT COUNT(*) n FROM opportunities WHERE needs_enrichment = 1"
        ).fetchone()["n"]
    if not rows:
        return None
    by = {r["tier"]: r["n"] for r in rows}
    total = sum(by.values())
    parts = ", ".join(f"{by[t]} Tier {t}" for t in ("A", "B", "C") if by.get(t))
    line = f"📥 {total} tracked ({parts})"
    if held:
        line += f" · {held} held for enrichment"
    return line


def _funnel_footer_line(db_path: str, today: str) -> str | None:
    summary = weekly_funnel_summary(db_path, today)
    if not summary:
        return None
    order = ["applied", "contacted", "replied", "intro_made", "interview", "offer"]
    parts = [f"{summary[k]} {k}" for k in order if summary.get(k)]
    return "This week: " + " → ".join(parts) if parts else None


def _age_days(first_iso: str, today: str) -> int:
    d0 = datetime.date.fromisoformat(first_iso[:10])
    d1 = datetime.date.fromisoformat(today)
    return (d1 - d0).days


# ---------------------------------------------------------------------------
# Render + post (thin layer over ChatPort)
# ---------------------------------------------------------------------------

def render_summary_blocks(sections: list[Section], date_str: str) -> list[dict]:
    """Block Kit summary header — the day's shape in one scannable post."""
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"🎯 Daily Attack Queue — {date_str}"}}
    ]
    for s in sections:
        body = f"*{s.title}*"
        if s.lines:
            body += "\n" + "\n".join(s.lines)
        if s.cards:
            body += "\n" + "\n".join(f"• {c['subject']}" for c in s.cards)
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})
    return blocks


def post_daily_queue(
    db_path: str,
    chat: "ChatPort",
    now: dt | None = None,
    career_facts: "CareerFacts | None" = None,
) -> dict:
    """Post the day's queue exactly once, cards threaded under the summary root.

    Idempotency (Q26): claim the date row FIRST (like Relay's sent_receipts
    claim). A duplicate fire finds the claim and no-ops — Josh never sees two
    queues. A crash after the claim but before posting leaves that day without a
    queue (self-heal logs it), which is preferable to a duplicate.
    """
    now = now or dt.now(timezone.utc)
    today = now.date().isoformat()
    now_iso = now.isoformat()

    try:
        with cursor(db_path) as cur:
            cur.execute(
                "INSERT INTO daily_queue (date, root_ts, posted_at) VALUES (?, NULL, ?)",
                (today, now_iso),
            )
    except sqlite3.IntegrityError:
        with cursor(db_path) as cur:
            row = cur.execute(
                "SELECT root_ts FROM daily_queue WHERE date = ?", (today,)
            ).fetchone()
        return {"ok": True, "skipped": True, "root_ts": row["root_ts"] if row else None}

    sections = build_sections(db_path, now=now, career_facts=career_facts)
    root = chat.post_blocks(
        f"Daily Attack Queue — {today}", render_summary_blocks(sections, today)
    )
    root_ts = root.get("ts")

    posted = 0
    with transaction(db_path) as cur:
        cur.execute("UPDATE daily_queue SET root_ts = ? WHERE date = ?", (root_ts, today))
        for s in sections:
            for c in s.cards:
                ref = c.get("draft_ref")
                if not ref:
                    continue
                draft = Draft(kind=c["kind"], to=c.get("to", ""),
                              subject=c.get("subject", ""), body=c.get("body", ""))
                res = chat.post_blocks(
                    f"[DRAFT — {c['kind']}] {c['subject']}",
                    render_draft_blocks(draft, ref),
                    thread_ts=root_ts,
                )
                card_ts = res.get("ts")
                cur.execute(
                    "INSERT INTO queue_items "
                    "(draft_ref, category, state, first_surfaced_at, last_surfaced_at, card_ts) "
                    "VALUES (?, ?, 'active', ?, ?, ?) "
                    "ON CONFLICT(draft_ref) DO UPDATE SET "
                    "last_surfaced_at = excluded.last_surfaced_at, card_ts = excluded.card_ts",
                    (ref, s.category, now_iso, now_iso, card_ts),
                )
                posted += 1

    return {"ok": True, "skipped": False, "root_ts": root_ts, "cards": posted}
