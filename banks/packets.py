"""Decision Packets + Action Queue (v2 mechanics, inherited "per v2" by Banks).

Every question Banks raises takes this shape: decision, recommendation, one
alternative, evidence, dollar impact, reversibility, deadline, and a default
that fires if Josh never answers. "Decision answered" (Josh picked an option)
is tracked separately from "action completed" (Josh actually did it) —
approved-but-not-done items age publicly with dollars-at-risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .store import cursor


@dataclass(frozen=True)
class DecisionPacket:
    kind: str
    decision: str
    recommendation: str
    default_if_unanswered: str
    alternative: str | None = None
    evidence: str | None = None
    dollar_impact_cents: int | None = None
    reversible: bool = True
    deadline: str | None = None  # ISO datetime


_INSERT_PACKET = """
    INSERT INTO decision_packets
        (kind, decision, recommendation, alternative, evidence,
         dollar_impact_cents, reversible, deadline, default_if_unanswered,
         created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def create_packet(db_path: str, packet: DecisionPacket, cur=None) -> int:
    """Insert a Decision Packet, returning its id.

    Pass `cur` to join an existing `store.transaction()` — surfacing a draft
    must write the packet and its send intent atomically (candidate 5).
    """
    now = datetime.now(timezone.utc).isoformat()
    params = (
        packet.kind,
        packet.decision,
        packet.recommendation,
        packet.alternative,
        packet.evidence,
        packet.dollar_impact_cents,
        1 if packet.reversible else 0,
        packet.deadline,
        packet.default_if_unanswered,
        now,
    )
    if cur is not None:
        cur.execute(_INSERT_PACKET, params)
        return cur.lastrowid
    with cursor(db_path) as own:
        own.execute(_INSERT_PACKET, params)
        return own.lastrowid


def mark_answered(db_path: str, packet_id: int) -> None:
    """Josh picked an option. Does NOT mean the action is done."""
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE decision_packets SET answered_at = ? WHERE id = ?",
            (now, packet_id),
        )


def mark_completed(db_path: str, packet_id: int) -> None:
    """The action was actually carried out — distinct from being answered."""
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE decision_packets SET completed_at = ? WHERE id = ?",
            (now, packet_id),
        )


def apply_defaults_past_deadline(db_path: str, now: datetime | None = None) -> list[int]:
    """Reversible packets past their deadline with no answer execute their default.

    Missed decisions never freeze opportunities. Returns the ids defaulted.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            """
            SELECT id FROM decision_packets
            WHERE answered_at IS NULL
              AND deadline IS NOT NULL
              AND deadline <= ?
              AND reversible = 1
            """,
            (now_iso,),
        )
        ids = [row["id"] for row in cur.fetchall()]
        for packet_id in ids:
            cur.execute(
                "UPDATE decision_packets SET answered_at = ? WHERE id = ?",
                (now_iso, packet_id),
            )
    return ids


def aging_action_queue(db_path: str) -> list[dict]:
    """Approved-but-not-done items, oldest first, with dollars-at-risk.

    This is the "no day ends with an unsent hottest-3" surface — items that
    Josh answered but hasn't yet completed.
    """
    with cursor(db_path) as cur:
        cur.execute(
            """
            SELECT id, kind, decision, dollar_impact_cents, answered_at, deadline
            FROM decision_packets
            WHERE answered_at IS NOT NULL AND completed_at IS NULL
            ORDER BY answered_at ASC
            """
        )
        return [dict(row) for row in cur.fetchall()]
