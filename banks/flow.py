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
from .relay import enqueue_intent


@dataclass(frozen=True)
class Proposed:
    packet_id: int
    draft_ref: str
    post: dict


def propose(db_path: str, packet: DecisionPacket, draft: Draft, chat: ChatPort,
            send_channel: str = "none:internal", josh_email: str | None = None) -> Proposed:
    """Persist the decision, freeze the send intent, then post the draft.

    `send_channel` (R-D3) is fixed at draft time: email:praise / email:sendas
    (outbound, Relay sends on Approve) or none:internal (informational — Approve
    just acknowledges). The intent captures the frozen payload (R-D2) now.

    Detailed-financial drafts (#5): Slack shows only the redacted summary
    (render withholds numbers), and the FULL body is routed to Josh by email —
    so on Approve the numbers go to his inbox, never into the channel.
    """
    to_addr = draft.to
    if draft.detailed_financial and send_channel == "none:internal" and josh_email:
        send_channel = "email:sendas"
        to_addr = josh_email

    packet_id = create_packet(db_path, packet)
    draft_ref = str(packet_id)
    enqueue_intent(db_path, draft_ref, send_channel,
                   to_addr=to_addr, subject=draft.subject, body=draft.body)
    post = chat.post_draft(draft, draft_ref)
    return Proposed(packet_id=packet_id, draft_ref=draft_ref, post=post)


def redraft(db_path: str, packet_id: int, new_draft: Draft, chat: ChatPort,
            send_channel: str = "none:internal") -> Proposed:
    """#3 revise loop: replace a revised draft with a fresh one on the SAME packet.

    Re-freezes the send intent (status back to pending) and re-posts. The
    decision packet is reused — one decision, a corrected draft.
    """
    draft_ref = str(packet_id)
    enqueue_intent(db_path, draft_ref, send_channel,
                   to_addr=new_draft.to, subject=new_draft.subject, body=new_draft.body)
    post = chat.post_draft(new_draft, draft_ref)
    return Proposed(packet_id=packet_id, draft_ref=draft_ref, post=post)
