"""MOD-05 threaded NL revisions (Q5/Q11/Q21).

A reply in a still-pending draft's thread — "shorter", "less formal", "stronger
hook" — rewrites the card IN PLACE via redraft() (same packet, re-frozen intent,
no drift). Fires only when both hold:
  (a) the reply is on a pending Banks draft card (card_ts → draft_ref), and
  (b) the router classifies revise-intent.
Otherwise Banks stays silent.

No-embellishment guard (constitution): the rewrite prompt gets career-facts as
the ONLY fact source, and a post-rewrite check FLAGS any number the rewrite
introduced that isn't in the facts or the original draft — "stronger hook"
cannot become embellishment.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import TYPE_CHECKING

from .approval import record_correction
from .enforcement import Draft
from .flow import redraft
from .refs import DraftRef, SendChannel
from .store import cursor

if TYPE_CHECKING:
    from .chatport import ChatPort
    from .opportunity import CareerFacts

_REVISION_KEYWORDS = (
    "shorter", "longer", "less formal", "more formal", "stronger hook",
    "punchier", "tighten", "expand", "warmer", "more direct", "softer",
)

_REVISION_SYSTEM = (
    "You rewrite a job-search outreach draft per the user's instruction "
    "(tone / length / structure ONLY). Use ONLY the facts provided as the fact "
    "source. Do NOT introduce any fact, number, title, company, or claim that is "
    "not already present in the facts or the current draft. Return only the "
    "rewritten body."
)

_REVISE_ROUTER_SYSTEM = (
    "Classify a Slack thread reply about a draft. Return JSON only: "
    '{"intent": "revise|question|none", "instruction": "<the edit or null>"}. '
    "revise = asks to change the draft; question = asks something; none = neither."
)


_REVISION_TTL_MIN = 15


def set_pending_revision(db_path: str, user_id: str, draft_ref: str) -> None:
    """Tap Revise → this user's next message is the instruction for draft_ref.

    One slot per user, last-tap-wins (INSERT OR REPLACE): tapping Revise on a
    second card before typing simply retargets to the second — what a human
    expects.
    """
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO pending_revisions (user_id, draft_ref, set_at) "
            "VALUES (?, ?, ?)",
            (user_id, str(draft_ref), now),
        )


def get_pending_revision(db_path: str, user_id: str, now: _dt.datetime | None = None,
                         ttl_min: int = _REVISION_TTL_MIN) -> str | None:
    """Return the pending draft_ref for this user if set and not expired.

    Expired slots are cleared and None returned — a message typed long after a
    forgotten Revise tap should not silently edit a stale draft.
    """
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT draft_ref, set_at FROM pending_revisions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        set_at = _dt.datetime.fromisoformat(row["set_at"])
    except ValueError:
        set_at = now
    if (now - set_at) > _dt.timedelta(minutes=ttl_min):
        clear_pending_revision(db_path, user_id)
        return None
    return row["draft_ref"]


def clear_pending_revision(db_path: str, user_id: str) -> None:
    with cursor(db_path) as cur:
        cur.execute("DELETE FROM pending_revisions WHERE user_id = ?", (user_id,))


def is_revision_context(db_path: str, card_ts: str) -> str | None:
    """Map a card's message ts → its draft_ref, iff still pending & active.

    Slack threads one level deep, so in production a card is a thread root and
    the reply's parent ts is the card ts. Returns None when the ts isn't a live
    Banks card (so random thread chatter is ignored).
    """
    if not card_ts:
        return None
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT qi.draft_ref FROM queue_items qi "
            "JOIN send_intents si ON si.draft_ref = qi.draft_ref "
            "WHERE qi.card_ts = ? AND qi.state = 'active' AND si.status = 'pending'",
            (card_ts,),
        ).fetchone()
    return row["draft_ref"] if row else None


def classify_revision(text: str, llm=None) -> tuple[str, str]:
    """Return (intent, instruction). Keyword fast-path, LLM fallback."""
    t = (text or "").strip()
    low = t.lower()
    for kw in _REVISION_KEYWORDS:
        if kw in low:
            return "revise", t
    if llm is not None:
        try:
            data = llm.extract_json(
                _REVISE_ROUTER_SYSTEM, t,
                schema_hint='{"intent":"revise|question|none","instruction":"str"}',
            )
            intent = data.get("intent", "none")
            if intent in ("revise", "question", "none"):
                return intent, data.get("instruction") or t
        except Exception:
            pass
    return "none", t


def apply_revision(
    db_path: str,
    draft_ref: DraftRef | str,
    instruction: str,
    career_facts: "CareerFacts",
    llm,
    chat: "ChatPort",
) -> dict:
    """Rewrite the pending draft in place, guarded against embellishment.

    Returns {"ok": True, "body": <new>} on success, or {"ok": False, "reason":
    ...} when there's no pending draft or the rewrite introduced a new fact.
    """
    ref = DraftRef.parse(draft_ref)
    with cursor(db_path) as cur:
        si = cur.execute(
            "SELECT subject, body, to_addr, send_channel FROM send_intents "
            "WHERE draft_ref = ? AND status = 'pending'",
            (str(ref),),
        ).fetchone()
    if not si:
        return {"ok": False, "reason": "no_pending_draft"}

    original = si["body"] or ""
    facts_block = _facts_block(career_facts)
    user = (
        f"Instruction: {instruction}\n\n"
        f"Facts (the ONLY permitted fact source):\n{facts_block}\n\n"
        f"Current draft:\n{original}"
    )
    revised = llm.complete(_REVISION_SYSTEM, user).strip()

    flag = _embellishment_flag(revised, facts_block, original)
    if flag:
        return {"ok": False, "reason": "embellishment", "detail": flag}

    new_draft = Draft(
        kind="revision", to=si["to_addr"] or "",
        subject=si["subject"] or "", body=revised,
    )
    redraft(db_path, ref.packet_id, new_draft, chat,
            send_channel=SendChannel.parse(si["send_channel"]))
    record_correction(db_path, ref.packet_id, "wrong_tone",
                      note=f"NL revision: {instruction[:120]}")
    return {"ok": True, "body": revised}


def _facts_block(career_facts: "CareerFacts") -> str:
    parts = []
    if career_facts.identity:
        parts.append(career_facts.identity)
    if career_facts.experience:
        parts.append("Experience: " + "; ".join(career_facts.experience))
    if career_facts.skills:
        parts.append("Skills: " + ", ".join(career_facts.skills))
    if career_facts.education:
        parts.append("Education: " + "; ".join(career_facts.education))
    if career_facts.ventures:
        parts.append("Ventures: " + "; ".join(career_facts.ventures))
    if career_facts.seeking:
        parts.append("Seeking: " + career_facts.seeking)
    return "\n".join(parts)


def _embellishment_flag(revised: str, facts_block: str, original: str) -> str | None:
    """Flag any number in the rewrite that isn't in the facts or the original.

    Numbers are the sharpest embellishment risk ("led $50M in ARR"). A flagged
    rewrite is NOT posted — Banks refuses rather than invent.
    """
    corpus = (facts_block + " " + (original or "")).lower()
    for num in re.findall(r"\d[\d,\.]*", revised):
        if num.lower() not in corpus:
            return f"introduced a number not in your facts: {num}"
    return None
