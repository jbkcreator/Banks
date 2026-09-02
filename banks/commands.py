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

import difflib
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import date

from .clock import today_local_iso
from .governance import network_activation_due
from .store import cursor
from .warmpath import describe_contact, find_referral_paths

_ROUTER_SYSTEM = (
    "You classify a Slack message into one job-search intent, or none. "
    "Return JSON only: "
    '{"intent": "whoat|status|calllist|pipeline|cant_do|stop_company|replied|'
    'unfreeze_company|none", "company": "<name or null>"}. '
    "whoat = who does the user know at a company; status = pipeline status of ONE "
    "company; calllist = who to reach out to today; pipeline = an overall snapshot "
    "of where they stand across all applications ('where am I', 'how's my search'); "
    "cant_do = asking Banks to browse LinkedIn or act outside its read-only "
    "job-search scope (which it cannot do); "
    "stop_company = the user wants to STOP pursuing / chasing / following up with ONE "
    "company, however softly phrased ('I don\'t want to keep chasing Acme', 'put a pin "
    "in Acme', 'drop them', 'Acme ghosted me, forget it'); "
    "replied = the user is telling you a company or its recruiter GOT BACK TO THEM / "
    "answered / responded, however narratively phrased ('the Acme recruiter finally "
    "replied', 'heard from Acme at last'); "
    "unfreeze_company = the user wants to RESUME/UN-FREEZE/re-start chasing a company "
    "that was previously stopped, however phrased ('actually keep going with Acme', "
    "'resume Acme', 'I take that back on Acme, follow up again', 'unfreeze Acme'). "
    "For stop_company, replied, and unfreeze_company you MUST return the company name; "
    "if the message only says 'them'/'they'/'it' with no company named, return intent "
    "none. Otherwise none. No other intents exist."
)
# Read-only intents the LLM may resolve straight to an answer.
_LLM_INTENTS = ("whoat", "status", "calllist", "pipeline", "cant_do")
# Mutating intents the LLM may only PROPOSE — see Command.source and the
# confirm-before-freeze path in socket_listener._handle_app_mention. An LLM
# guess must never write a freeze/unfreeze unattended.
_LLM_MUTATION_INTENTS = ("stop_company", "replied", "unfreeze_company")

_MENU = (
    "• `@banks where am I` — a snapshot of your whole pipeline\n"
    "• `@banks status Acme` — where one company stands\n"
    "• `@banks who do I know at Acme` — warm intros there\n"
    "• `@banks call list` — who to reach out to today\n"
    "• `@banks replied Acme` — stop all follow-ups there\n"
    "• `@banks resume chasing Acme` — undo a freeze, restart follow-ups\n"
    "• `@banks anything come in?` — job-search email from the last 14 days"
)
_HELP = "Here's what I can pull up for you:\n" + _MENU


@dataclass(frozen=True)
class Command:
    intent: str            # whoat | status | calllist | replied | none
    company: str | None = None
    raw: str = ""          # original text, so the `none` fallback can be context-aware
    source: str = "keyword"  # "keyword" (deterministic regex) | "llm" (classified)


# Things Josh may expect a general chat-bot to do that Banks deliberately cannot —
# reading his live inbox or browsing LinkedIn would breach the hard wall. When he
# asks, answer honestly instead of dumping the command menu at him.
_CANT_DO = re.compile(
    # includes common misspellings (linkdin/linkedn/gmial) — keyword matching is
    # brittle to typos, and full NL understanding is out of scope (see the client
    # scope doc §3: "advanced conversational Slack commands" are deferred).
    r"\b(linked ?in|linkd?in|linkedn|linkedln|lnkedin|"
    r"browse|scrape|log ?in)\b", re.IGNORECASE,
)
_GREETING = re.compile(r"^\s*(hi|hey|hello|yo|good\s+(morning|afternoon|evening)|gm)\b",
                       re.IGNORECASE)
_ASKED_HELP = re.compile(r"\b(help|what can you|what do you|commands?|options?)\b",
                         re.IGNORECASE)

_CANT_DO_REPLY = (
    "Here's what I can pull up for you:\n" + _MENU +
    "\n\n_(I read your inbox for job-search mail only — replies from companies you "
    "applied to, recruiters, and job boards, going back 14 days. I don't see the "
    "rest of your mail, and I can't browse LinkedIn.)_"
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
    return ("Not sure what you mean. I handle: `@banks who do I know at <company>`, "
            "`@banks status <company>`, `@banks call list`, `@banks replied <company>`. "
            "Say `@banks help` for more.")


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

    # Unfreeze — the mirror of stop/replied. No un-freeze command existed at all
    # until 2026-09-02: "@banks resume" restarts standing jobs but never touched
    # company_freeze, so a company frozen by mistake or reconsidered stayed
    # frozen forever with no way back short of editing the database.
    m = re.search(r"(?:resume|unfreeze|un-freeze|start)\s+(?:chasing\s+|"
                  r"following up (?:with|on)\s+|contacting\s+|pursuing\s+)?(.+)",
                  low)
    if m:
        return Command("unfreeze_company", _clean_company(m.group(1)))

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
                schema_hint='{"intent":"whoat|status|calllist|pipeline|cant_do|'
                            'stop_company|replied|none","company":"str|null"}',
            )
            intent = data.get("intent", "none")
            if intent in _LLM_INTENTS or intent in _LLM_MUTATION_INTENTS:
                company = _clean_company(data.get("company")) if data.get("company") else None
                # A mutation the LLM inferred from soft phrasing needs a named
                # company AND Josh's confirmation (source="llm" → _apply_freeze
                # proposes instead of writing). Without a company it is not
                # actionable at all, so fall through to `none`.
                if intent in _LLM_MUTATION_INTENTS:
                    if not company or is_pronoun_reference(company):
                        return Command("none", raw=t)
                    return Command(intent, company, raw=t, source="llm")
                return Command(intent, company, raw=t)
        except Exception:
            pass

    return Command("none", raw=t)


def handle_command(db_path: str, cmd: Command) -> str:
    """Dispatch a routed command to a formatted, human-readable reply."""
    if cmd.intent == "whoat":
        if not cmd.company:
            return "Which company? Try `@banks who do I know at Acme`."
        return who_do_i_know_text(db_path, cmd.company)

    if cmd.intent == "calllist":
        contacts = network_activation_due(db_path, today_local_iso(), limit=5)
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
        return _apply_freeze(db_path, cmd, "stop_company")

    if cmd.intent == "unfreeze_company":
        return _apply_unfreeze(db_path, cmd)

    if cmd.intent == "pipeline":
        return _pipeline_summary(db_path)

    if cmd.intent == "status":
        if not cmd.company:
            return "Which company? Try `@banks status Acme`."
        return _company_status(db_path, cmd.company)

    if cmd.intent == "replied":
        return _apply_freeze(db_path, cmd, "replied")

    return fallback_reply(cmd.raw)


def who_do_i_know_text(db_path: str, company: str) -> str:
    """Warm contacts at a company. Typo-tolerant — shared by the command router
    and the QA layer's who_do_i_know tool so both behave identically."""
    slug, note = _resolve_for_read(db_path, company,
                                   "@banks who do I know at {c}")
    if slug is None and note:
        return note
    target = slug or company
    paths = find_referral_paths(db_path, target)
    if not paths:
        return f"No known contacts at {target}."
    head = f"{note}Who you know at {target}:"
    return head + "\n" + "\n".join(f"• {describe_contact(c)}" for c in paths)


def _apply_freeze(db_path: str, cmd: Command, kind: str) -> str:
    """Shared gate + write for the two freezing intents (stop_company/replied).

    Three gates run BEFORE anything is written:
      1. a company must be named — never a bare pronoun ("stop chasing them"
         froze a company literally called "them");
      2. it must resolve EXACTLY to a company Banks tracks — otherwise the write
         is junk and the reply tells Josh his follow-ups stopped when they did
         not (this is what happened live on 2026-09-02 with the row
         "evolve — stop chasing them", while `evolve` itself kept running);
      3. a soft, LLM-classified phrasing is CONFIRMED, not applied — a freeze has
         no un-freeze command, and the build's spine is propose-then-approve.
    """
    template = ("@banks stop chasing {c}" if kind == "stop_company"
                else "@banks replied {c}")
    if not cmd.company:
        return f"Which company? Try `{template.format(c='Acme')}`."

    match = resolve_company(db_path, cmd.company)
    if not match.exact:
        return _did_you_mean(match, cmd.company, template)

    if cmd.source == "llm":
        verb = ("stop chasing" if kind == "stop_company"
                else "log a reply from and freeze")
        return (f"Just to confirm — want me to {verb} *{match.slug}*, stopping all "
                f"follow-ups there? Send `{template.format(c=match.slug)}` and it's "
                f"done. _(Nothing has changed yet.)_")

    from .governance import record_reply
    n = record_reply(db_path, match.slug)
    opps = f"{n} opportunit{'y' if n == 1 else 'ies'}"
    if kind == "stop_company":
        return (f"🧊 Stopped chasing *{match.slug}* — all follow-ups there frozen "
                f"({opps}). Everything else is still running.")
    return (f"🧊 Froze *{match.slug}* — reply logged. All pending follow-ups there "
            f"stopped ({opps}). No one who replied will be chased.")


def _apply_unfreeze(db_path: str, cmd: Command) -> str:
    """Counterpart to _apply_freeze — resume follow-ups at a frozen company.

    Until 2026-09-02 this had NO command at all: "@banks resume" only lifts the
    global kill switch (halt.py), it never touched company_freeze, so a company
    frozen by mistake or reconsidered stayed frozen forever with no way back
    short of editing the database by hand — which is what actually happened
    live (hari froze Evolve, said "resume", and it stayed frozen).

    Same gates as _apply_freeze: a company must be named and must resolve
    exactly (typo-tolerant via resolve_company); an LLM-inferred phrasing is
    confirmed via socket_listener's pending-confirmation flow before this is
    ever called with cmd.source == "llm" in production — this branch stays
    only so handle_command() behaves consistently if called directly.
    """
    from .governance import unfreeze_company
    template = "@banks resume chasing {c}"
    if not cmd.company:
        return f"Which company? Try `{template.format(c='Acme')}`."

    match = resolve_company(db_path, cmd.company)
    if not match.exact:
        return _did_you_mean(match, cmd.company, template)

    if cmd.source == "llm":
        return (f"Just to confirm — want me to resume chasing *{match.slug}*? "
                f"Send `{template.format(c=match.slug)}` and it's done. "
                f"_(Nothing has changed yet.)_")

    if unfreeze_company(db_path, match.slug):
        return (f"▶️ Resumed *{match.slug}* — follow-ups there are back on. "
                f"Any cadence touches that were frozen are re-queued.")
    return f"*{match.slug}* wasn't frozen — nothing to resume."


def _resolve_for_read(db_path: str, typed: str, template: str):
    """Resolve a company for a READ. Returns (slug_or_None, note_or_message).

    Reads are safe to act on a single close match (with a visible note); a wrong
    read costs nothing, whereas dead-ending on a one-letter typo is the bug.
    """
    match = resolve_company(db_path, typed)
    if match.exact:
        return match.slug, ""
    if match.pronoun:
        return None, _did_you_mean(match, typed, template)
    if len(match.suggestions) == 1:
        return match.suggestions[0], f"_(reading that as *{match.suggestions[0]}*)_\n"
    if match.suggestions:
        return None, _did_you_mean(match, typed, template)
    return None, ""      # nothing close — caller emits its own "not tracked"


def _pipeline_summary(db_path: str) -> str:
    """Deterministic one-glance pipeline snapshot from the DB (no LLM, no invented
    numbers). Answers "where am I with applying?" — Josh's recurring question."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT tier, needs_enrichment FROM opportunities").fetchall()
        frozen = cur.execute("SELECT COUNT(*) c FROM company_freeze").fetchone()["c"]
    if not rows:
        return ("No applications tracked yet. Drop a Simplify export here, "
                "tag me with a JD PDF (@banks + attach), and I'll start building "
                "the pipeline. Intake email is monitored automatically.")
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
        f"_Ask `@banks status Acme` for one company, or `@banks call list` for today's outreach._"
    )


def _company_status(db_path: str, company: str) -> str:
    """Pipeline snapshot — pure read: tier, lanes, warm-intro, cadence, freeze, contacts."""
    from .normalise import normalise_company
    slug, note = _resolve_for_read(db_path, company, "@banks status {c}")
    if slug is None and note:
        return note
    slug = slug or normalise_company(company) or company.lower()

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
        f"{note}*{opp['title']}* — Tier {opp['tier']}"
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


# Pronouns a human uses for "the company we were just talking about". Banks holds
# no conversation state, so these can never be resolved — they must ASK, never
# guess and never freeze. ("ok stop chasing them" once froze a company literally
# named "them".)
_PRONOUNS = {
    "them", "they", "it", "that", "this", "those", "these", "him", "her",
    "that one", "this one", "the first one", "the second one", "the last one",
    "there", "the other one",
}

# Greedy `(.+)` captures swallow whatever trails the company name. Cut at the
# first clause boundary so "replied Evolve — stop chasing them" resolves to
# "evolve", not to a company called "evolve — stop chasing them" (which is
# exactly what landed in the live DB on 2026-09-02).
_CLAUSE_BREAK = re.compile(r"\s+[—–]\s*|\s+-\s+|[,;:]")
_LEADING_FILLER = re.compile(
    r"^(?:the|with|on|at|about|to|for|from|any|an?)\s+", re.IGNORECASE)
_TRAILING_FILLER = re.compile(
    r"\s+(?:for now|for the moment|anymore|any more|any longer|please|thanks|"
    r"thank you|too|as well|ok|okay|yet|already|finally)$", re.IGNORECASE)


def _clean_company(raw: str | None) -> str | None:
    if not raw:
        return None
    s = _CLAUSE_BREAK.split(raw.strip(), maxsplit=1)[0]
    s = re.sub(r"[?.!,]+$", "", s.strip()).strip()
    s = _LEADING_FILLER.sub("", s).strip()
    prev = None
    while prev != s:                      # "Acme for now please" -> "Acme"
        prev = s
        s = _TRAILING_FILLER.sub("", s).strip()
    s = re.sub(r"[?.!,]+$", "", s).strip()
    return s or None


def is_pronoun_reference(name: str | None) -> bool:
    """True when the 'company' is really a back-reference to an earlier turn."""
    return bool(name) and name.strip().lower() in _PRONOUNS


# ---------------------------------------------------------------------------
# Company resolution (typo tolerance + the anti-garbage guard on freezes)
# ---------------------------------------------------------------------------

_FUZZY_CUTOFF = 0.80


@dataclass(frozen=True)
class CompanyMatch:
    """Result of resolving a user-typed company name against tracked companies.

    exact=True means the slug is a company Banks actually tracks — the ONLY case
    in which a mutation (freeze) is allowed to write.
    """
    slug: str | None                       # resolved tracked slug, else None
    exact: bool = False
    suggestions: tuple[str, ...] = ()      # close matches when exact misses
    pronoun: bool = False


def known_companies(db_path: str) -> list[str]:
    """Every company slug Banks tracks — opportunities first, then contacts."""
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT DISTINCT company_normalized AS c FROM opportunities "
            "WHERE company_normalized IS NOT NULL AND company_normalized != '' "
            "UNION "
            "SELECT DISTINCT company AS c FROM contacts "
            "WHERE company IS NOT NULL AND company != ''"
        ).fetchall()
    return [r["c"] for r in rows]


def resolve_company(db_path: str, raw: str | None) -> CompanyMatch:
    """Resolve a typed company name to a tracked slug, tolerating typos.

    Exact normalised hit wins. Otherwise fall back to fuzzy + substring matching
    so "Ripling" finds "rippling" instead of dead-ending. A fuzzy hit is returned
    as a *suggestion*, never as an exact match: reads may act on it with a note,
    mutations must confirm first (a freeze has no undo).
    """
    from .normalise import normalise_company

    if not raw:
        return CompanyMatch(None)
    if is_pronoun_reference(raw):
        return CompanyMatch(None, pronoun=True)

    slug = normalise_company(raw)
    if not slug:
        return CompanyMatch(None)

    names = known_companies(db_path)
    if slug in names:
        return CompanyMatch(slug, exact=True)

    # Substring both ways catches "appfolio inc" / "folio" style near-misses that
    # difflib ratio alone scores too low.
    subs = [n for n in names if slug in n or n in slug]
    fuzzy = difflib.get_close_matches(slug, names, n=3, cutoff=_FUZZY_CUTOFF)
    seen: list[str] = []
    for n in fuzzy + subs:
        if n not in seen:
            seen.append(n)
    return CompanyMatch(None, suggestions=tuple(seen[:3]))


def _did_you_mean(match: CompanyMatch, typed: str, template: str) -> str:
    """Shared 'I couldn't resolve that company' reply. `template` is a command
    example with {c} where the company goes."""
    if match.pronoun:
        return (f"Which company? `{typed}` doesn't tell me who you mean on its "
                f"own — name it, e.g. `{template.format(c='Acme')}`.")
    if match.suggestions:
        opts = " or ".join(f"`{template.format(c=s)}`" for s in match.suggestions)
        return f"I don't track *{typed}*. Did you mean {opts}?"
    return (f"I don't track a company called *{typed}*, so there's nothing to "
            f"change. Check the name with `@banks where am I`.")


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
