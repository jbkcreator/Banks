"""Short conversation memory for the @banks QA layer.

Before this, every mention was answered from scratch: "what's the status of
Ketch?" then "who do I know there?" had no referent, and the model either
re-asked or picked a company — a confident answer about the wrong employer
(found 2026-09-02).

Two rules, in order:
  1. Carry a few recent turns so ordinary follow-ups just work.
  2. When a question still needs a company and none can be resolved, ASK.
     Never guess. `needs_clarification()` is what enforces that.

Scope is per user and time-boxed: memory older than TURN_TTL_MIN is not
context, it's a stale assumption, and resolving "there" against a company from
an hour ago is the same failure as guessing.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .store import cursor

MAX_TURNS = 6          # how many recent turns to carry
TURN_TTL_MIN = 30      # older than this is a new conversation, not context

# Words that only make sense against an earlier turn.
_REFERRING = re.compile(
    r"\b(there|them|they|it|that one|those|him|her|the first one|the second one|"
    r"the last one|the other one|same (?:one|company)|this one)\b", re.IGNORECASE)

# Tools whose answer is meaningless without a company.
COMPANY_TOOLS = frozenset({"company_status", "who_do_i_know"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_turn(db_path: str, user_id: str, question: str, answer: str,
                companies: list[str] | None = None) -> None:
    """Store one Q/A exchange, then prune this user's history to MAX_TURNS."""
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO qa_turns (user_id, question, answer, companies, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, question or "", (answer or "")[:2000],
             ",".join(companies or []), _now().isoformat()),
        )
        cur.execute(
            "DELETE FROM qa_turns WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM qa_turns WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, MAX_TURNS),
        )


def recent_turns(db_path: str, user_id: str) -> list[dict]:
    """This user's recent turns, oldest first. Stale turns are dropped."""
    cutoff = (_now() - timedelta(minutes=TURN_TTL_MIN)).isoformat()
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT question, answer, companies, created_at FROM qa_turns "
            "WHERE user_id = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
            (user_id, cutoff, MAX_TURNS),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def companies_in_context(db_path: str, user_id: str) -> list[str]:
    """Companies named in recent turns, most recent first, de-duplicated."""
    seen: list[str] = []
    for turn in reversed(recent_turns(db_path, user_id)):
        for slug in (turn["companies"] or "").split(","):
            slug = slug.strip()
            if slug and slug not in seen:
                seen.append(slug)
    return seen


def is_referring(text: str) -> bool:
    """True if the question leans on an earlier turn ('who do I know there')."""
    return bool(_REFERRING.search(text or ""))


def format_context(turns: list[dict]) -> str:
    """Render prior turns for the routing/compose prompts."""
    if not turns:
        return ""
    lines = ["Earlier in this conversation (most recent last):"]
    for t in turns:
        lines.append(f"  Josh asked: {t['question']}")
        lines.append(f"  You answered: {(t['answer'] or '')[:300]}")
    return "\n".join(lines)


def needs_clarification(tool: str, args: dict, context_companies: list[str]) -> str | None:
    """Return a question to ask Josh, or None if the call can proceed.

    A company-scoped tool with no company, and nothing in context to resolve it
    against, must NOT be called with a guess — an authoritative-sounding answer
    about the wrong company is worse than one more question.
    """
    if tool not in COMPANY_TOOLS:
        return None
    company = (args.get("company") or "").strip()
    if company:
        return None
    if len(context_companies) == 1:
        return None          # unambiguous — the caller resolves it from context
    if len(context_companies) > 1:
        opts = ", ".join(f"*{c}*" for c in context_companies[:4])
        return f"Which one did you mean — {opts}?"
    return "Which company do you mean?"


def resolve_company(args: dict, context_companies: list[str]) -> dict:
    """Fill an omitted company from context when exactly one candidate exists."""
    if (args.get("company") or "").strip():
        return args
    if len(context_companies) == 1:
        return {**args, "company": context_companies[0]}
    return args
