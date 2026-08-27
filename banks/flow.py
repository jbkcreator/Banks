"""The draft lifecycle — one door for surfacing a decision and acting on it.

A domain event (a vacancy, an inquiry, a bill due) becomes a Decision Packet
plus a frozen send intent, then posts its Block Kit draft to `#banks` with
buttons carrying the DraftRef. Clicking a button comes back through
`apply_action`, re-exported here so callers have a single interface for both
directions of the lifecycle rather than sequencing four peer modules.

**Atomicity (candidate 5).** Surfacing writes two rows — the packet and its
intent — inside ONE transaction. Previously they were two, so a crash between
them left a packet with no intent: a draft that could be approved but could
never send. Approve-and-never-send is the client's stated worst case, so the
invariant "a packet always has an intent" is enforced by the store, not by hope.

This module is the correct place to add gates that must apply to *every* draft
(the Phase I permanent suppression list and 48h touch-log collision check):
one chokepoint, already carrying all six surfacings.
"""

from __future__ import annotations

from dataclasses import dataclass

from .approval import ButtonAction, apply_action  # re-exported: one lifecycle door
from .chatport import ChatPort
from .contacts import check_contact_discipline
from .exclusion import DraftExcluded, is_target_excluded
from .enforcement import Draft
from .packets import DecisionPacket, create_packet
from .refs import DraftRef, SendChannel
from .relay import enqueue_intent
from .store import transaction

__all__ = ["Proposed", "propose", "redraft", "apply_action", "ButtonAction"]


@dataclass(frozen=True)
class Proposed:
    packet_id: int
    ref: DraftRef
    post: dict

    @property
    def draft_ref(self) -> str:
        """Back-compat: the bare id callers and Slack payloads still use."""
        return str(self.ref)


def propose(db_path: str, packet: DecisionPacket, draft: Draft, chat: ChatPort,
            send_channel: SendChannel | str = SendChannel.INTERNAL,
            josh_email: str | None = None,
            company: str | None = None, contact: dict | None = None) -> Proposed:
    """Persist the decision, freeze the send intent, then post the draft.

    `send_channel` (R-D3) is fixed at draft time: email:praise / email:sendas
    (outbound, Relay sends on Approve) or none:internal (informational — Approve
    just acknowledges). The intent captures the frozen payload (R-D2) now.

    Detailed-financial drafts (#5): Slack shows only the redacted summary
    (render withholds numbers), and the FULL body is routed to Josh by email —
    so on Approve the numbers go to his inbox, never into the channel.

    `company`/`contact` (MOD-06): when a caller knows the draft's target, this
    every-draft chokepoint enforces the exclusion wall here — the same funnel
    that already enforces the suppression wall — via the single
    exclusion.is_target_excluded predicate. Raises DraftExcluded if blocked.
    """
    channel = SendChannel.parse(send_channel)
    to_addr = draft.to
    if draft.detailed_financial and channel is SendChannel.INTERNAL and josh_email:
        channel = SendChannel.SENDAS
        to_addr = josh_email

    # Exclusion wall (MOD-06): one predicate, this every-draft chokepoint.
    if company or contact:
        excluded, reason = is_target_excluded(db_path, company=company, contact=contact)
        if excluded:
            raise DraftExcluded(reason or "target on the exclusion wall")

    # Contact discipline gate (T2-8): suppression + 48h touch-log check.
    # Outbound drafts only — internal drafts have no external recipient.
    if channel.is_outbound:
        check_contact_discipline(db_path, to_addr)

    # Packet + intent commit together, or neither does (candidate 5).
    with transaction(db_path) as cur:
        packet_id = create_packet(db_path, packet, cur=cur)
        ref = DraftRef(packet_id)
        enqueue_intent(db_path, ref, channel, to_addr=to_addr,
                       subject=draft.subject, body=draft.body, cur=cur)

    # Posting is deliberately OUTSIDE the transaction: a Slack failure must not
    # roll back a persisted decision, it must leave the draft surfaceable again.
    post = chat.post_draft(draft, str(ref))
    return Proposed(packet_id=packet_id, ref=ref, post=post)


def redraft(db_path: str, packet_id: int, new_draft: Draft, chat: ChatPort,
            send_channel: SendChannel | str = SendChannel.INTERNAL) -> Proposed:
    """#3 revise loop: replace a revised draft with a fresh one on the SAME packet.

    Re-freezes the send intent (status back to pending) and re-posts. The
    decision packet is reused — one decision, a corrected draft.
    """
    ref = DraftRef(packet_id)
    enqueue_intent(db_path, ref, SendChannel.parse(send_channel),
                   to_addr=new_draft.to, subject=new_draft.subject, body=new_draft.body)
    post = chat.post_draft(new_draft, str(ref))
    return Proposed(packet_id=packet_id, ref=ref, post=post)
