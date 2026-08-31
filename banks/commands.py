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
    "You classify a Slack message into one job-search retrieval intent, or none. "
    "Return JSON only: "
    '{"intent": "whoat|status|calllist|pipeline|cant_do|none", "company": "<name or null>"}. '
    "whoat = who does the user know at a company; status = pipeline status of ONE "
    "company; calllist = who to reach out to today; pipeline = an overall snapshot "
    "of where they stand across all applications ('where am I', 'how's my search'); "
    "cant_do = asking Banks to read their live inbox or browse LinkedIn/Gmail "
    "(which it cannot do). Otherwise none. No other intents exist."
)
_LLM_INTENTS = ("whoat", "status", "calllist", "pipeline", "cant_do")

_MENU = (
    "• `where am I` — a snapshot of your whole pipeline\n"
    "• `status Acme` — where one company stands\n"
    "• `who do I know at Acme` — warm intros there\n"
    "• `call list` — who to reach out to today\n"
    "• `replied Acme` — stop all follow-ups there"
)
_HELP = "Here's what I can pull up for you:\n" + _MENU


@dataclass(frozen=True)
class Command:
    intent: str            # whoat | status | calllist | replied | none
    company: str | None = None
    raw: str = ""          # original text, so the `none` fallback can be context-aware


# Things Josh may expect a general chat-bot to do that Banks deliberately cannot —
# reading his live inbox or browsing LinkedIn would breach the hard wall. When he
# asks, answer honestly instead of dumping the command menu at him.
_CANT_DO = re.compile(
    # includes common misspellings (linkdin/linkedn/gmial) — keyword matching is
    # brittle to typos, and full NL understanding is out of scope (see the client
    # scope doc §3: "advanced conversational Slack commands" are deferred).
    r"\b(linked ?in|linkd?in|linkedn|linkedln|lnkedin|gmail|gmial|inbox|e-?mail|"
    r"browse|scrape|log ?in|read my|see my|look (?:through|at) my|check my|"
    r"go through my)\b", re.IGNORECASE,
)
_GREETING = re.compile(r"^\s*(hi|hey|hello|yo|good\s+(morning|afternoon|evening)|gm)\b",
                       re.IGNORECASE)
_ASKED_HELP = re.compile(r"\b(help|what can you|what do you|commands?|options?)\b",
                         re.IGNORECASE)

_CANT_DO_REPLY = (
    "Here's what I can pull up for you:\n" + _MENU +
    "\n\n_(I can't read your live inbox or browse LinkedIn/Gmail — that's the hard "
    "wall by design; I only see application confirmations you forward me.)_"
)


def fallback_reply(text: str) -> str:
    """Context-aware reply for an unrecognised message — never the blind menu.

    Capability question -> honest 'I can't (hard wall)'. Greeting -> short hello.
    Explicit help ask -> the full menu. Anything else -> a one-line nudge.
    """
    t = text or ""
    if _CANT_DO.search(t):
        return _CANT_DO_REPLY
    if _ASKED_HELP.search(t):
        return _HELP
    if _GREETING.search(t):
        return "Morning. I'm your job-search command surface — say `help` to see what I do."
    return ("Not sure what you mean. I handle: `who do I know at <company>`, "
            "`status <company>`, `call list`, `replied <company>`. Say `help` for more.")


def route(db_path: str, text: str, llm=None) -> Command:
    """Keyword fast-path first; LLM fallback only if it misses and llm is given."""
    t = (text or "").strip()
    low = t.lower()

    m = re.search(r"who\s+do\s+i\s+know\s+at\s+(.+)", low)
    if m:
        return Command("whoat", _clean_company(m.group(1)))

    if "call list" in low or "who should i reach out" in low or "reach out to today" in low:
        return Command("calllist")

    # Pipeline snapshot — Josh's real recurring question ("where am I with
    # applying?"). Deterministic counts from the DB, no LLM, no company needed.
    if re.search(r"where am i|where do i stand|how am i doing|my pipeline|"
                 r"pipeline (?:snapshot|status|overview)|update on (?:my )?applications?|"
                 r"overview of|where.*applications? stand", low):
        return Command("pipeline")

    # Reply-safety trigger (review #8): "replied Acme" / "got a reply from Acme"
    # freezes that company's cadence so nobody who answered gets a follow-up.
    m = re.search(r"(?:got a reply from|replied|reply from|heard back from)\s+(.+)", low)
    if m:
        return Command("replied", _clean_company(m.group(1)))

    # Targeted stop — "stop chasing Acme" freezes ONE company (not a global halt;
    # halt.is_halt_command already excludes this shape). Same freeze effect as a
    # reply, so nothing more goes out to that company.
    m = re.search(r"stop (?:chasing|pursuing|contacting|following up(?: on| with)?|"
                  r"reaching out to)\s+(.+)", low)
    if m:
        return Command("stop_company", _clean_company(m.group(1)))
    m = re.search(r"^stop\s+(.+)", low)   # "stop Acme" (global stop intercepted upstream)
    if m:
        return Command("stop_company", _clean_company(m.group(1)))

    m = re.search(r"\bstatus\s+(?:of\s+|on\s+)?(.+)", low)
    if m:
        return Command("status", _clean_company(m.group(1)))

    # Capability question (LinkedIn/Gmail/inbox) — honest "can't, hard wall".
    # Keyword fast-path (typo-tolerant); the LLM below also maps to cant_do.
    if _CANT_DO.search(low):
        return Command("cant_do", raw=t)

    if llm is not None:
        try:
            data = llm.extract_json(
                _ROUTER_SYSTEM, t,
                schema_hint='{"intent":"whoat|status|calllist|pipeline|cant_do|none","company":"str|null"}',
            )
            intent = data.get("intent", "none")
            if intent in _LLM_INTENTS:
                company = _clean_company(data.get("company")) if data.get("company") else None
                return Command(intent, company, raw=t)
        except Exception:
            pass

    return Command("none", raw=t)


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

    if cmd.intent == "cant_do":
        return _CANT_DO_REPLY

    if cmd.intent == "stop_company":
        if not cmd.company:
            return "Which company? Try `stop chasing Acme`."
        from .normalise import normalise_company
        from .governance import record_reply
        n = record_reply(db_path, normalise_company(cmd.company))
        return (f"🧊 Stopped chasing *{cmd.company}* — all follow-ups there frozen "
                f"({n} opportunit{'y' if n == 1 else 'ies'}). "
                f"Everything else is still running.")

    if cmd.intent == "pipeline":
        return _pipeline_summary(db_path)

    if cmd.intent == "status":
        if not cmd.company:
            return "Which company? Try `status Acme`."
        return _company_status(db_path, cmd.company)

    if cmd.intent == "replied":
        if not cmd.company:
            return "Which company? Try `replied Acme`."
        from .normalise import normalise_company
        from .governance import record_reply
        n = record_reply(db_path, normalise_company(cmd.company))
        return (f"🧊 Froze {cmd.company} — reply logged. All pending follow-ups there "
                f"stopped ({n} opportunit{'y' if n == 1 else 'ies'}). No one who replied "
                f"will be chased.")

    return fallback_reply(cmd.raw)


def _pipeline_summary(db_path: str) -> str:
    """Deterministic one-glance pipeline snapshot from the DB (no LLM, no invented
    numbers). Answers "where am I with applying?" — Josh's recurring question."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT tier, needs_enrichment FROM opportunities").fetchall()
        frozen = cur.execute("SELECT COUNT(*) c FROM company_freeze").fetchone()["c"]
    if not rows:
        return ("No applications tracked yet. Forward a confirmation to your intake "
                "email, or drop a Simplify export in this channel, and I'll start "
                "building the pipeline.")
    total = len(rows)
    held = sum(1 for r in rows if r["needs_enrichment"])
    surfaced = [r for r in rows if not r["needs_enrichment"]]
    a = sum(1 for r in surfaced if r["tier"] == "A")
    b = sum(1 for r in surfaced if r["tier"] == "B")
    c = sum(1 for r in surfaced if r["tier"] == "C")
    noun = "opportunity" if total == 1 else "opportunities"
    return (
        f"*Your pipeline — {total} {noun} tracked:*\n"
        f"• Scored & surfaced: {len(surfaced)}  (Tier A {a}, B {b}, C {c})\n"
        f"• Held for enrichment (need comp/industry): {held}\n"
        f"• Companies frozen — you replied, follow-ups stopped: {frozen}\n"
        f"_Ask `status Acme` for one company, or `call list` for today's outreach._"
    )


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
