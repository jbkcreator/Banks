"""Confirm-before-freeze for inferred mutations (MOD-05, 2026-09-02).

Freezing a company is the only thing an @banks *message* can mutate, and it is
consequential: it stops every follow-up there. Two failures made it unsafe:

  - Paraphrases no-opped. "let's put a pin in Acme for now" matched no regex,
    fell through to the read-only QA layer, and Josh got a conversational reply
    while the cadence kept firing. He believed he'd given an instruction.
  - Greedy captures froze nothing. "replied Evolve stop chasing them" stored the
    company as "evolve stop chasing them"; a real freeze row was written for a
    company that does not exist, and Slack said "🧊 Froze Evolve".

So: the LLM may now *propose* a freeze, but an inferred one is confirmed before
it fires, and every freeze — inferred or matched — must name a company Banks
actually tracks. Exact command phrasings stay immediate; they're unambiguous.
"""
from __future__ import annotations

import datetime as _dt
import difflib

from .normalise import normalise_company
from .store import cursor

CONFIRM_TTL_MIN = 10

_YES = {"yes", "y", "yeah", "yep", "yup", "confirm", "confirmed", "do it",
        "go ahead", "correct", "right", "ok", "okay", "sure", "please do"}
_NO = {"no", "n", "nope", "nah", "cancel", "never mind", "nevermind", "stop",
       "don't", "dont", "forget it", "leave it"}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# ---------------------------------------------------------------------------
# Does Banks actually track this company?

def known_companies(db_path: str) -> list[str]:
    with cursor(db_path) as cur:
        return [r["company_normalized"] for r in cur.execute(
            "SELECT DISTINCT company_normalized FROM opportunities "
            "WHERE company_normalized IS NOT NULL AND company_normalized != ''")]


def resolve_known_company(db_path: str, company: str | None) -> tuple[str | None, list[str]]:
    """(exact slug, close matches). A freeze must never be written for a company
    Banks doesn't track — that's how "🧊 Froze Evolve" froze nothing."""
    if not company:
        return None, []
    slug = normalise_company(company) or company.strip().lower()
    known = known_companies(db_path)
    if slug in known:
        return slug, []
    # A greedy capture ("evolve stop chasing them") contains the real name.
    contained = [k for k in known if k and (k in slug or slug.startswith(k + " "))]
    if len(contained) == 1:
        return contained[0], []
    close = difflib.get_close_matches(slug, known, n=3, cutoff=0.6)
    return None, sorted(set(contained + close))


# ---------------------------------------------------------------------------
# Pending confirmation slot (one per user, last-ask-wins)

def set_pending_confirmation(db_path: str, user_id: str, intent: str,
                             company: str, raw: str = "") -> None:
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO pending_confirmations "
            "(user_id, intent, company, raw, set_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, intent, company, raw, _now().isoformat()),
        )


def get_pending_confirmation(db_path: str, user_id: str) -> dict | None:
    with cursor(db_path) as cur:
        row = cur.execute(
            "SELECT intent, company, raw, set_at FROM pending_confirmations "
            "WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    try:
        age = (_now() - _dt.datetime.fromisoformat(row["set_at"])).total_seconds()
    except ValueError:
        return None
    if age > CONFIRM_TTL_MIN * 60:
        clear_pending_confirmation(db_path, user_id)
        return None
    return dict(row)


def clear_pending_confirmation(db_path: str, user_id: str) -> None:
    with cursor(db_path) as cur:
        cur.execute("DELETE FROM pending_confirmations WHERE user_id = ?", (user_id,))


def read_confirmation(text: str) -> bool | None:
    """True = go ahead, False = drop it, None = not an answer to the question.

    Unrecognised replies return None so the pending freeze is left alone rather
    than being taken as consent — silence is not a yes for a mutation.
    """
    t = (text or "").strip().lower().rstrip("!.,")
    if t in _YES:
        return True
    if t in _NO:
        return False
    return None


def confirmation_prompt(intent: str, company: str) -> str:
    what = ("you heard back from" if intent == "replied" else "you want to stop chasing")
    # The tag is not optional: untagged messages are ignored by design, so
    # "Reply yes" alone got a bare "yes" dropped in live testing (2026-09-02)
    # while Josh believed he had confirmed the freeze.
    return (f"Just to confirm — {what} *{company}*? "
            f"That freezes every follow-up there.\n"
            f"Reply `@banks yes` to confirm, or `@banks no` to leave it running.")


def unknown_company_reply(company: str | None, suggestions: list[str]) -> str:
    name = company or "that company"
    if suggestions:
        opts = ", ".join(f"*{s}*" for s in suggestions)
        return (f"I don't track *{name}*. Did you mean {opts}? "
                f"Nothing has been frozen.")
    return (f"I don't have any opportunity for *{name}*, so there's nothing to "
            f"freeze. Check the name, or ask `@banks where am I` for what I track.")
