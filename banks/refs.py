"""Identity and routing value types for a draft (architecture candidate 2).

Two facts used to be stringly-typed and spread across five modules:

* **draft_ref** — minted as `str(packet_id)` in flow, stored as TEXT by relay,
  cast back with `int()` by approval, re-parsed out of a Slack `block_id` by
  reactions, and unknown to packets. A rowid laundered through a string.
* **outbound-ness** — answered three different ways: a hand-typed channel
  literal at each call site, `startswith("email:")` in relay, and an
  overridable bool in approval. A typo in a channel literal made an outbound
  draft silently internal — it never failed, it just never sent, which is the
  exact failure the client named as his worst case.

Both now have one representation. This module is a leaf: it imports nothing
from the package, so it can be used anywhere without creating a cycle.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class SendChannel(enum.Enum):
    """Where an approved draft goes. Fixed at draft time (R-D3), never inferred later."""

    #: Praise (the property manager) sends onward to tenants/vendors — C-D1.
    PRAISE = "email:praise"
    #: Straight to Josh's own inbox — carries detailed financial bodies (#5).
    SENDAS = "email:sendas"
    #: Informational. Approve acknowledges; Relay must never fire.
    INTERNAL = "none:internal"

    @property
    def is_outbound(self) -> bool:
        """True when approving this draft hands it to Relay to actually send.

        The single source of truth. Replaces the prefix test in relay and the
        override parameter in approval.
        """
        return self.value.startswith("email:")

    @classmethod
    def parse(cls, raw: "SendChannel | str | None") -> "SendChannel":
        """Coerce a stored/legacy string. Unknown values fail loudly.

        Deliberately strict: an unrecognised channel used to mean "not
        outbound" (silence). Now it raises, because silence was the bug.
        """
        if isinstance(raw, cls):
            return raw
        if raw is None:
            raise ValueError("send_channel is required; there is no safe default")
        for member in cls:
            if member.value == raw:
                return member
        raise ValueError(
            f"unknown send_channel {raw!r} — expected one of "
            f"{[m.value for m in cls]}. A typo here previously caused a draft to "
            f"be approved but never sent."
        )


@dataclass(frozen=True, order=True)
class DraftRef:
    """A draft's identity: the decision-packet id, carried as a type.

    Renders as the bare id so every existing store row, Slack `block_id` and
    log line stays byte-identical — this is a typing change, not a data change.
    """

    packet_id: int

    def __str__(self) -> str:
        return str(self.packet_id)

    @classmethod
    def parse(cls, raw: "DraftRef | str | int") -> "DraftRef":
        """Coerce from a Slack payload, a DB column, or an int packet id."""
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, bool):  # bool is an int subclass — reject explicitly
            raise TypeError(f"draft_ref cannot be a bool: {raw!r}")
        if isinstance(raw, int):
            return cls(raw)
        text = str(raw).strip()
        if not text.isdigit():
            raise ValueError(f"draft_ref must be a decision-packet id, got {raw!r}")
        return cls(int(text))
