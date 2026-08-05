"""propose() — the draft→packet→post flow (wires domain events to #banks).

A domain event (a vacancy, an inquiry, a bill due) becomes a DecisionPacket in
the store, then posts its Block Kit draft to #banks with buttons whose
draft_ref is the packet id. That closes the loop with the approval handler
(approval.apply_action) and the aging brief (briefing) — same draft_ref
throughout. This is the single place domain modules call to surface a decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chatport import ChatPort
from .enforcement import Draft
from .packets import DecisionPacket, create_packet


@dataclass(frozen=True)
class Proposed:
    packet_id: int
    draft_ref: str
    post: dict


def propose(db_path: str, packet: DecisionPacket, draft: Draft, chat: ChatPort) -> Proposed:
    """Persist the decision, then post its draft with draft_ref = packet id."""
    packet_id = create_packet(db_path, packet)
    draft_ref = str(packet_id)
    post = chat.post_draft(draft, draft_ref)
    return Proposed(packet_id=packet_id, draft_ref=draft_ref, post=post)
