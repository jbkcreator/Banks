"""Morning briefing — constitution-ordered (Phase I complete, Part 5 job 1).

Section order (B-D1 + Phase I):
  1. Approved but not sent (failure-mode-first)
  2. Today's pre-ranked 1-3
  3. Vacancy
  4. Money due (7-day)
  5. Collections overdue
  6. Deadline radar
  7. Yesterday recap
  8. Schedule + prep (today's calendar events)
  9. ROI this week
  10. Daily Find
  11. Daily scorecard line
  12. Market brief staleness (B-D2)
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from .activity_log import hours_saved_this_week, recent_events
from .briefport import brief_section_lines
from .find import find_brief_lines
from .packets import aging_action_queue
from .schedule import OpportunityCostInputs, roi_line, weekly_roi
from .store import cursor


def _age(iso: str | None, now: datetime) -> str:
    if not iso:
        return "—"
    then = datetime.fromisoformat(iso)
    hrs = (now - then).total_seconds() / 3600
    if hrs < 24:
        return f"{hrs:.0f}h"
    return f"{hrs / 24:.0f}d"


def _yesterday_recap_lines(db_path: str, now: datetime) -> list[str]:
    since = (now - timedelta(hours=24)).isoformat()
    events = recent_events(db_path, limit=50)
    yesterday = [e for e in events if e["ts"] >= since]
    if not yesterday:
        return ["Nothing logged yesterday."]
    counts: Counter = Counter(e["kind"] for e in yesterday)
    return [f"• {kind.replace('_', ' ')}: {n}" for kind, n in counts.most_common()]


def _schedule_lines(now: datetime, calendar=None) -> list[str]:
    if calendar is not None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        try:
            events = calendar.events(day_start, day_end)
            if not events:
                return ["No events today."]
            return [f"• {e.title} ({e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')})"
                    for e in events]
        except Exception as exc:
            return [f"Calendar unavailable: {exc}"]
    return ["(CalendarPort not wired — schedule unavailable)"]


def _deadline_radar_lines(db_path: str, now: datetime) -> list[str]:
    """Forward-looking radar: decision deadlines, promises, lease ends, bills."""
    horizon = (now + timedelta(days=7)).date().isoformat()
    today = now.date().isoformat()
    lines: list[str] = []

    with cursor(db_path) as cur:
        cur.execute(
            "SELECT decision, deadline, dollar_impact_cents FROM decision_packets "
            "WHERE deadline IS NOT NULL AND deadline <= ? AND answered_at IS NULL "
            "ORDER BY deadline ASC LIMIT 5",
            (horizon + "T23:59:59",),
        )
        for r in cur.fetchall():
            lines.append(f"⏰ Decision: {r['decision'][:60]} "
                         f"(due {r['deadline'][:10]}, "
                         f"${(r['dollar_impact_cents'] or 0)/100:,.0f})")

        cur.execute(
            "SELECT description, due_at FROM promises "
            "WHERE due_at IS NOT NULL AND due_at <= ? AND status = 'open' "
            "ORDER BY due_at ASC LIMIT 5",
            (horizon,),
        )
        for r in cur.fetchall():
            lines.append(f"📋 Promise: {r['description'][:60]} (due {r['due_at'][:10]})")

        lease_horizon = (now + timedelta(days=30)).date().isoformat()
        cur.execute(
            "SELECT unit_label, property_address, lease_end FROM rooms "
            "WHERE lease_end IS NOT NULL AND lease_end <= ? AND lease_end >= ? "
            "ORDER BY lease_end ASC",
            (lease_horizon, today),
        )
        for r in cur.fetchall():
            lines.append(f"🏠 Lease ending: {r['unit_label']} at {r['property_address']} "
                         f"({r['lease_end']})")

        cur.execute(
            "SELECT name, due_date, amount_cents FROM bills "
            "WHERE due_date <= ? AND due_date >= ? AND on_time IS NULL "
            "ORDER BY due_date ASC LIMIT 5",
            (horizon, today),
        )
        for r in cur.fetchall():
            lines.append(f"💸 Bill: {r['name']} "
                         f"${(r['amount_cents'] or 0)/100:,.0f} on {r['due_date']}")

    return lines or ["Nothing on the radar in the next 7 days."]


def _collections_overdue_lines(db_path: str) -> list[str]:
    from .collections import overdue_charges
    overdue = overdue_charges(db_path)
    if not overdue:
        return ["✓ All rent current."]
    total_cents = sum(c["amount_cents"] for c in overdue)
    lines = [f"⚠️ {len(overdue)} overdue charge(s) — ${total_cents/100:,.0f} outstanding"]
    for c in overdue[:5]:
        lines.append(f"  • {c['unit_label']} at {c['property_address']}: "
                     f"${c['amount_cents']/100:,.0f} due {c['due_date']}")
    return lines


def _daily_scorecard_line(db_path: str, now: datetime) -> list[str]:
    today = now.date().isoformat()
    with cursor(db_path) as cur:
        drafts_today = cur.execute(
            "SELECT COUNT(*) AS n FROM decision_packets WHERE created_at >= ?", (today,)
        ).fetchone()["n"]
        approved_today = cur.execute(
            "SELECT COUNT(*) AS n FROM decision_packets WHERE answered_at >= ?", (today,)
        ).fetchone()["n"]
        sent_today = cur.execute(
            "SELECT COUNT(*) AS n FROM send_intents "
            "WHERE status = 'sent' AND created_at >= ?", (today,)
        ).fetchone()["n"]
        overdue_decisions = cur.execute(
            "SELECT COUNT(*) AS n FROM decision_packets "
            "WHERE answered_at IS NULL AND deadline IS NOT NULL AND deadline <= ?",
            (now.isoformat(),),
        ).fetchone()["n"]
    parts = [f"Drafts: {drafts_today}", f"Approved: {approved_today}", f"Sent: {sent_today}"]
    if overdue_decisions:
        parts.append(f"⚠️ {overdue_decisions} overdue decision(s)")
    return [" · ".join(parts)]


def brief_sections(db_path: str, now: datetime | None = None,
                   calendar=None) -> list[tuple[str, list[str]]]:
    """Constitution-ordered (title, lines) sections. Pure/testable."""
    now = now or datetime.now(timezone.utc)
    sections: list[tuple[str, list[str]]] = []

    # 1. FAILURE-MODE-FIRST: approved but not yet sent.
    aging = aging_action_queue(db_path)
    if aging:
        lines = [
            f"⚠️ {a['decision']} — approved {_age(a['answered_at'], now)} ago, "
            f"not marked sent (${(a['dollar_impact_cents'] or 0)/100:,.0f})"
            for a in aging
        ]
    else:
        lines = ["✓ Nothing approved-but-unsent."]
    sections.append(("Approved but not sent", lines))

    with cursor(db_path) as cur:
        # 2. Today's pre-ranked 1-3.
        cur.execute(
            "SELECT decision, dollar_impact_cents FROM decision_packets "
            "WHERE answered_at IS NULL ORDER BY dollar_impact_cents DESC LIMIT 3"
        )
        top = cur.fetchall()
        sections.append((
            "Today's top 1-3",
            [f"• {r['decision']} (${(r['dollar_impact_cents'] or 0)/100:,.0f})" for r in top]
            or ["• Nothing pending a decision."],
        ))

        # 3. Vacancy.
        cur.execute("SELECT COUNT(*) AS n FROM rooms WHERE occupied = 0")
        vacant = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM rooms")
        total = cur.fetchone()["n"]
        sections.append(("Vacancy", [f"{vacant} vacant of {total} rooms"]))

        # 4. Money due (7-day window).
        cur.execute(
            "SELECT name, amount_cents, due_date FROM bills "
            "WHERE due_date <= date('now', '+7 day') ORDER BY due_date ASC"
        )
        due = cur.fetchall()
        sections.append((
            "Money due (7-day)",
            [f"• {b['name']}: ${(b['amount_cents'] or 0)/100:,.0f} due {b['due_date']}"
             for b in due] or ["• None."],
        ))

    # 5. Collections.
    sections.append(("Collections", _collections_overdue_lines(db_path)))

    # 6. Deadline radar.
    sections.append(("Deadline radar", _deadline_radar_lines(db_path, now)))

    # 7. Yesterday recap.
    sections.append(("Yesterday", _yesterday_recap_lines(db_path, now)))

    # 8. Schedule + prep.
    sections.append(("Today's schedule", _schedule_lines(now, calendar)))

    # 9. ROI meter.
    hrs = hours_saved_this_week(db_path, now)
    roi = weekly_roi(OpportunityCostInputs(hours_saved=hrs))
    sections.append(("ROI this week", [roi_line(roi)]))

    # 10. Daily Find.
    sections.append(("Daily Find", find_brief_lines(db_path, now.date().isoformat())))

    # 11. Daily scorecard line.
    sections.append(("Today's scorecard", _daily_scorecard_line(db_path, now)))

    # 12. Market brief staleness (B-D2).
    sections.append(("Market brief", brief_section_lines(db_path, now)))

    return sections


def render_brief_blocks(db_path: str, now: datetime | None = None,
                        calendar=None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    header = f"Banks — Morning Brief ({now.date().isoformat()})"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}}
    ]
    for title, lines in brief_sections(db_path, now, calendar=calendar):
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*\n" + "\n".join(lines)},
        })
    return blocks
