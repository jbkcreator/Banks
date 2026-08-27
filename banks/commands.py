"""MOD-05 on-demand commands — hybrid intent router (Q6/Q16/Q22).

Two layers (researched pattern for a tightly-scoped 3-intent domain):
  Layer 1 — keyword fast-path: handles the common phrasings, zero LLM cost,
            works even if the key is down.
  Layer 2 — LLM extract_json fallback with a small intent enum: absorbs typos
            and phrasing. The LLM never sees a large tool list, so it stays in
            the high-accuracy regime.

Stays inside the plan's "core retrieval actions" — it parses intent, it does not
become an open-ended conversational agent (explicitly out of scope).
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import date

from .governance import network_activation_due
from .store import cursor
from .warmpath import describe_contact, find_referral_paths

_ROUTER_SYSTEM = (
    "You classify a Slack message into one of three job-search retrieval "
    "intents, or none. Return JSON only: "
    '{"intent": "whoat|status|calllist|none", "company": "<name or null>"}. '
    "whoat = who does the user know at a company; status = pipeline status of a "
    "company; calllist = who to reach out to today. No other intents exist."
)

_HELP = (
    "I can look things up:\n"
    "• `who do I know at <company>`\n"
    "• `status <company>`\n"
    "• `call list`"
)


@dataclass(frozen=True)
class Command:
    intent: str            # whoat | status | calllist | none
    company: str | None = None


def route(db_path: str, text: str, llm=None) -> Command:
    """Keyword fast-path first; LLM fallback only if it misses and llm is given."""
    t = (text or "").strip()
    low = t.lower()

    m = re.search(r"who\s+do\s+i\s+know\s+at\s+(.+)", low)
    if m:
        return Command("whoat", _clean_company(m.group(1)))

    if "call list" in low or "who should i reach out" in low or "reach out to today" in low:
        return Command("calllist")

    m = re.search(r"\bstatus\s+(?:of\s+|on\s+)?(.+)", low)
    if m:
        return Command("status", _clean_company(m.group(1)))

    if llm is not None:
        try:
            data = llm.extract_json(
                _ROUTER_SYSTEM, t,
                schema_hint='{"intent":"whoat|status|calllist|none","company":"str|null"}',
            )
            intent = data.get("intent", "none")
            if intent in ("whoat", "status", "calllist"):
                return Command(intent, _clean_company(data.get("company")) if data.get("company") else None)
        except Exception:
            pass

    return Command("none")


def handle_command(db_path: str, cmd: Command) -> str:
    """Dispatch a routed command to a formatted, human-readable reply."""
    if cmd.intent == "whoat":
        if not cmd.company:
            return "Which company? Try `who do I know at Acme`."
        paths = find_referral_paths(db_path, cmd.company)
        if not paths:
            return f"No known contacts at {cmd.company}."
        head = f"Who you know at {cmd.company}:"
        return head + "\n" + "\n".join(f"• {describe_contact(c)}" for c in paths)

    if cmd.intent == "calllist":
        contacts = network_activation_due(db_path, date.today().isoformat(), limit=5)
        if not contacts:
            return "Nobody's due — everyone's been touched in the last 14 days."
        lines = []
        for c in contacts:
            role = c.get("title") or c.get("position") or ""
            lines.append(f"• {c.get('name') or 'contact'}{f' ({role})' if role else ''}")
        return "Today's call list:\n" + "\n".join(lines)

    if cmd.intent == "status":
        if not cmd.company:
            return "Which company? Try `status Acme`."
        return _company_status(db_path, cmd.company)

    return _HELP


def _company_status(db_path: str, company: str) -> str:
    """Pipeline snapshot — pure read: tier, lanes, warm-intro, cadence, freeze, contacts."""
    from .normalise import normalise_company
    slug = normalise_company(company) or company.lower()

    with cursor(db_path) as cur:
        opp = cur.execute(
            "SELECT id, title, tier, pursuit_mode, status FROM opportunities "
            "WHERE company_normalized = ? ORDER BY id DESC LIMIT 1",
            (slug,),
        ).fetchone()
        if not opp:
            return f"No opportunity tracked for {company}."
        opp_id = opp["id"]
        lanes = cur.execute(
            "SELECT lane_type, status FROM outreach_lanes WHERE opportunity_id = ? ORDER BY id",
            (opp_id,),
        ).fetchall()
        intro = cur.execute(
            "SELECT state FROM warm_intros WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        next_touch = cur.execute(
            "SELECT MIN(cq.due_date) d FROM cadence_queue cq "
            "JOIN outreach_lanes ol ON ol.id = cq.outreach_lane_id "
            "WHERE ol.opportunity_id = ? AND cq.status = 'pending'",
            (opp_id,),
        ).fetchone()
        frozen = cur.execute(
            "SELECT thaw_at FROM company_freeze WHERE company_normalized = ?", (slug,)
        ).fetchone()
        contacts = cur.execute(
            "SELECT name, title FROM contacts WHERE company = ? ORDER BY id", (slug,)
        ).fetchall()

    parts = [
        f"*{opp['title']}* — Tier {opp['tier']}"
        + (f" · {opp['pursuit_mode']}" if opp["pursuit_mode"] else "")
        + f" · {opp['status']}"
    ]
    if lanes:
        parts.append("Lanes: " + ", ".join(f"{l['lane_type']}={l['status']}" for l in lanes))
    else:
        parts.append("Lanes: none yet")
    if intro:
        parts.append(f"Warm intro: {intro['state']}")
    parts.append(f"Next follow-up: {next_touch['d']}" if next_touch and next_touch["d"]
                 else "Next follow-up: none scheduled")
    parts.append("🔥 In conversation (frozen)" if frozen else "Not frozen")
    if contacts:
        contact_bits = []
        for c in contacts:
            role = f" ({c['title']})" if c["title"] else ""
            contact_bits.append(f"{c['name']}{role}")
        parts.append("Contacts: " + ", ".join(contact_bits))
    return "\n".join(parts)


def _clean_company(raw: str | None) -> str | None:
    if not raw:
        return None
    return re.sub(r"[?.!,]+$", "", raw.strip()).strip()


# ---------------------------------------------------------------------------
# Cost guard (Q22) — light rolling cap on LLM-fallback calls per user
# ---------------------------------------------------------------------------

class RateLimiter:
    """Rolling per-user cap. Cheap insurance against loops / fat-finger spam —
    not a quota system. Keyword commands short-circuit before ever reaching here.
    """

    def __init__(self, max_calls: int = 20, window_s: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window_s = window_s
        self._hits: dict[str, deque] = {}

    def allow(self, user_id: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        dq = self._hits.setdefault(user_id, deque())
        while dq and now - dq[0] > self.window_s:
            dq.popleft()
        if len(dq) >= self.max_calls:
            return False
        dq.append(now)
        return True
