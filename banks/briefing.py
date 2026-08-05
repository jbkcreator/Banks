"""Morning briefing in B-D1 order — failure-mode-first (Part 5 job 1, live).

B-D1 fixed the ordering: the most likely failure — a draft Josh approved but
never actually sent — leads, with age on each item, before anything else. Then
today's pre-ranked 1-3, vacancy, money due, schedule, yesterday, one learning
item, scorecard line, and finally a market-brief staleness flag (B-D2).

Renders against whatever is in the store; posts to #banks as Block Kit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .packets import aging_action_queue
from .store import cursor


def _age(iso: str | None, now: datetime) -> str:
    if not iso:
        return "—"
    then = datetime.fromisoformat(iso)
    hrs = (now - then).total_seconds() / 3600
    if hrs < 24:
        return f"{hrs:.0f}h"
    return f"{hrs / 24:.0f}d"


def brief_sections(db_path: str, now: datetime | None = None) -> list[tuple[str, list[str]]]:
    """Ordered (title, lines) sections — B-D1 order. Pure/testable."""
    now = now or datetime.now(timezone.utc)
    sections: list[tuple[str, list[str]]] = []

    # 1. FAILURE-MODE-FIRST: approved but not yet sent, oldest first, with age.
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
        # 2. Today's pre-ranked 1-3 (unanswered, by dollar impact).
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

    # 8/9. Market-brief staleness flag (B-D2) — placeholder until BriefPort lands.
    sections.append(("Market brief", ["_No brief today — context flagged stale (B-D2)._"]))
    return sections


def render_brief_blocks(db_path: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    header = f"Banks — Morning Brief ({now.date().isoformat()})"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}}
    ]
    for title, lines in brief_sections(db_path, now):
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*\n" + "\n".join(lines)},
        })
    return blocks
