"""Kill command — halt flag that every job checks at entry (Phase I T3-14).

Josh sends "STOP ALL" or "STOP Banks" in #banks. The socket listener calls
set_halt(). Every job entry point calls check_halt() before doing any work —
halts within one cycle.

THE FLAG IS PERSISTED IN THE DB, and that is load-bearing. Banks runs as two
processes (banks-listener for buttons/messages, banks-scheduler for standing
jobs including relay_dispatch every 5 min). The flag used to be a module global,
which meant Josh's "stop all" set it in the *listener* while the *scheduler*
kept sending — the kill switch acknowledged the halt and stopped nothing
(found 2026-09-02). A shared row is the only thing both processes can see.

It also survives restart, deliberately: a deploy or a crash must not silently
resume outreach Josh stopped. Only "@banks resume" clears it.

`init_halt(db_path)` is called once at startup by container/run/listener. Until
then the module falls back to an in-process flag so tests and CLI tools that
never touch a DB still behave.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone

from .store import cursor

_lock = threading.Lock()
_db_path: str | None = None
# Fallback for callers that never called init_halt() (tests, one-shot scripts).
_halted: bool = False
_halt_reason: str = ""

# Exact phrases kept for back-compat; the matcher below is broader.
HALT_PHRASES = frozenset({
    "stop all",
    "stop banks",
})

# A missed halt is the one truly unsafe outcome (Banks keeps sending when Josh
# wanted it stopped), so global-halt matching is deliberately broad + typo-
# tolerant. But a TARGETED stop ("stop chasing Acme") must NOT global-halt — it
# means freeze one company. Rule: it's a global halt only when a stop token is
# followed by nothing but global filler (all/everything/banks/now/…).
_STOP_TOKENS = {"stop", "stpo", "stahp", "halt", "hlt", "pause", "paus",
                "kill", "cease", "freeze", "shutdown", "abort"}
_GLOBAL_FILLER = {"all", "everything", "evrything", "everythin", "banks", "bank",
                  "now", "please", "the", "bots", "bot", "jobs", "job", "it",
                  "immediately", "right", "asap", "already", "just", "and",
                  "completely", "now.", "everythng"}
_UNHALT_TOKENS = {"resume", "unhalt", "unpause", "continue", "reactivate", "restart"}
_UNHALT_PHRASES = {"start banks", "start again", "banks resume", "go banks",
                   "resume banks", "turn back on"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", (text or "").lower())


class BanksHalted(RuntimeError):
    """Raised by check_halt() when the halt flag is set."""


def init_halt(db_path: str | None) -> None:
    """Point the kill switch at the shared DB. Called once at process startup.

    Without this the flag is per-process and the switch cannot cross the
    listener/scheduler boundary — the exact defect this module was rewritten to
    remove — so production entry points MUST call it.
    """
    global _db_path
    with _lock:
        _db_path = db_path


def _read_state() -> tuple[bool, str]:
    if _db_path is None:
        return _halted, _halt_reason
    try:
        with cursor(_db_path) as cur:
            row = cur.execute(
                "SELECT halted, reason FROM halt_state WHERE id = 1").fetchone()
    except Exception as exc:
        # Fail SAFE: if the flag can't be read we cannot prove Banks is allowed
        # to send, so treat it as halted rather than transmitting on a guess.
        return True, f"halt state unreadable ({exc})"
    if row is None:
        return False, ""
    return bool(row["halted"]), (row["reason"] or "")


def set_halt(reason: str = "operator command") -> None:
    global _halted, _halt_reason
    with _lock:
        _halted, _halt_reason = True, reason
        if _db_path is None:
            return
        with cursor(_db_path) as cur:
            cur.execute(
                "INSERT INTO halt_state (id, halted, reason, set_at) "
                "VALUES (1, 1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "halted = 1, reason = excluded.reason, set_at = excluded.set_at",
                (reason, datetime.now(timezone.utc).isoformat()),
            )


def clear_halt() -> None:
    """Lift the halt. Only "@banks resume" (approver-gated) should call this —
    a restart must NOT, or outreach Josh stopped resumes behind his back."""
    global _halted, _halt_reason
    with _lock:
        _halted, _halt_reason = False, ""
        if _db_path is None:
            return
        with cursor(_db_path) as cur:
            cur.execute(
                "INSERT INTO halt_state (id, halted, reason, set_at) "
                "VALUES (1, 0, '', ?) ON CONFLICT(id) DO UPDATE SET "
                "halted = 0, reason = '', set_at = excluded.set_at",
                (datetime.now(timezone.utc).isoformat(),),
            )


def is_halted() -> bool:
    with _lock:
        return _read_state()[0]


def halt_reason() -> str:
    with _lock:
        return _read_state()[1]


def check_halt() -> None:
    """Call at the top of every job. Raises BanksHalted if the flag is set."""
    with _lock:
        halted, reason = _read_state()
        if halted:
            raise BanksHalted(
                f"Banks is halted ({reason}). Say '@banks resume' to lift it."
            )


def is_halt_command(text: str) -> bool:
    """True if the message is a GLOBAL halt (typo/phrasing tolerant).

    Global only when a stop token is followed by nothing but global filler:
      'stop all', 'STOP ALL!', 'please stop everything now', 'halt', 'kill it' → True
      'stop chasing Acme', 'freeze Acme', 'stop reaching out to Beta'        → False
    """
    t = (text or "").strip().lower()
    if t in HALT_PHRASES:
        return True
    w = _words(t)
    idx = next((i for i, x in enumerate(w) if x in _STOP_TOKENS), None)
    if idx is None:
        return False
    after = w[idx + 1:]
    return all(x in _GLOBAL_FILLER for x in after)


def is_unhalt_command(text: str) -> bool:
    """True if the message is a GLOBAL resume (typo/phrasing tolerant).

    Same discipline as is_halt_command: global only when an unhalt token is
    followed by nothing but global filler. "resume Acme" / "resume chasing
    Acme" / "restart chasing Acme" name ONE company and must NOT lift the
    global halt — before this fix `any(x in _UNHALT_TOKENS for x in words)`
    matched on "resume" appearing ANYWHERE in the message, so those phrasings
    were swallowed here before commands.py's unfreeze_company regex ever saw
    them (found 2026-09-02, adding the unfreeze command exposed it).
    """
    t = (text or "").strip().lower().rstrip("!. ")
    if t in _UNHALT_PHRASES:
        return True
    w = _words(t)
    idx = next((i for i, x in enumerate(w) if x in _UNHALT_TOKENS), None)
    if idx is None:
        return False
    after = w[idx + 1:]
    return all(x in _GLOBAL_FILLER for x in after)
