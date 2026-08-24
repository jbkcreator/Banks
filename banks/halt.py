"""Kill command — halt flag that every job checks at entry (Phase I T3-14).

Josh sends "STOP ALL" or "STOP Banks" in #banks. Socket listener calls
set_halt(). Every job entry point calls check_halt() before doing any work —
halts within one cycle.

The flag is in-process (not persisted) so a restart clears it. This is
intentional: a restart is a deliberate resumption, not an accidental bypass.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_halted: bool = False
_halt_reason: str = ""

HALT_PHRASES = frozenset({
    "stop all",
    "stop banks",
})


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
    """True if the message is a recognised halt phrase."""
    return text.strip().lower() in HALT_PHRASES
