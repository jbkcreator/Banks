"""Kill command — halt flag that every job checks at entry (Phase I T3-14).

Josh sends "STOP ALL" or "STOP Banks" in #banks. Socket listener calls
set_halt(). Every job entry point calls check_halt() before doing any work —
halts within one cycle.

The flag is in-process (not persisted) so a restart clears it. This is
intentional: a restart is a deliberate resumption, not an accidental bypass.
"""

from __future__ import annotations

import re
import threading

_lock = threading.Lock()
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


def set_halt(reason: str = "operator command") -> None:
    global _halted, _halt_reason
    with _lock:
        _halted = True
        _halt_reason = reason


def clear_halt() -> None:
    """Reset the halt flag (used by tests and on restart)."""
    global _halted, _halt_reason
    with _lock:
        _halted = False
        _halt_reason = ""


def is_halted() -> bool:
    with _lock:
        return _halted


def check_halt() -> None:
    """Call at the top of every job. Raises BanksHalted if the flag is set."""
    with _lock:
        if _halted:
            raise BanksHalted(
                f"Banks is halted ({_halt_reason}). "
                "Restart the process to resume."
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
    """True if the message asks to resume after a halt. Approver-gated by the
    caller — re-enabling sends must not be open to anyone."""
    t = (text or "").strip().lower().rstrip("!. ")
    if t in _UNHALT_PHRASES:
        return True
    return any(x in _UNHALT_TOKENS for x in _words(t))
