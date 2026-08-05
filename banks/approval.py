"""Block Kit approval UI + click handling (ChatPort, E-D3 / A-D8 as buttons).

Supersedes emoji reactions as the *primary* approval mechanism: a draft posts to
`#banks` as a Block Kit message carrying four labelled buttons — Approve, Mark
sent, Reject, Revise. Each button's `value` is the draft_ref (A-D8 correlation,
now via button value rather than reaction). Clicks arrive over Socket Mode (no
public server); this module holds the *pure* logic so it is unit-testable
without Slack. Emoji reactions remain a fallback (see banks.reactions poller).

The two-step (Q6) is preserved: Approve = "decision answered", Mark sent =
"action completed" — tracked distinctly in decision_packets (packets.py).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .enforcement import Draft
from .packets import mark_answered, mark_completed


class ButtonAction(enum.Enum):
    """The four approval actions (A-D8 vocabulary, as buttons)."""

    APPROVE = "banks_approve"   # ✅ decision answered; Relay may send (if outbound)
    MARK_SENT = "banks_sent"    # 📤 Josh did it himself / confirm sent → completed
    REJECT = "banks_reject"     # ❌ drop it
    REVISE = "banks_revise"     # ✍️ redraft requested


# Human-facing labels (buttons) — kept next to the vocab so they stay in sync.
_LABELS = {
    ButtonAction.APPROVE: "✅ Approve",
    ButtonAction.MARK_SENT: "📤 Mark sent",
    ButtonAction.REJECT: "❌ Reject",
    ButtonAction.REVISE: "✍️ Revise",
}


def render_draft_blocks(draft: Draft, draft_ref: str) -> list[dict]:
    """Render a Draft as Block Kit blocks with the four approval buttons.

    Financial detail is withheld from the channel exactly as the text renderer
    does (enforcement.Draft.as_channel_message) — Slack never carries the numbers.
    """
    header = f"[DRAFT — {draft.kind}] {draft.subject}"
    if draft.detailed_financial:
        body = (
            "_Detailed financial matter — sent by email/attachment, not posted here._\n"
            f"Intended recipient (on your tap): {draft.to}"
        )
    else:
        body = f"{draft.body}\nIntended recipient (on your tap): {draft.to}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {
            "type": "actions",
            "block_id": f"approve::{draft_ref}",
            "elements": [
                {
                    "type": "button",
                    "action_id": action.value,
                    "text": {"type": "plain_text", "text": _LABELS[action]},
                    "value": draft_ref,
                    "style": (
                        "primary" if action is ButtonAction.APPROVE
                        else "danger" if action is ButtonAction.REJECT
                        else None
                    ),
                }
                for action in ButtonAction
                # buttons can't carry style=None; drop the key where unset
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"draft_ref `{draft_ref}`"}],
        },
    ]
    return _clean_styles(blocks)


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a click: what to show back + whether Relay should send."""

    status_text: str          # replaces the message so the thread reflects reality
    enqueue_send: bool = False  # Approve on an outbound draft → Relay picks it up


def _clean_styles(blocks: list[dict]) -> list[dict]:
    """Strip button style keys left None (Slack rejects null styles)."""
    for b in blocks:
        if b.get("type") == "actions":
            for el in b["elements"]:
                if el.get("style") is None:
                    el.pop("style", None)
    return blocks


def apply_action(
    db_path: str,
    action: ButtonAction,
    draft_ref: str,
    user_id: str,
    *,
    is_outbound: bool | None = None,
) -> ActionResult:
    """Apply a click to decision-packet + send-intent state. Pure w.r.t. Slack.

    draft_ref is the decision_packets id (as a string). Whether the draft is
    outbound is read from its send_intent (R-D3) — the send_channel fixed at
    draft time. `is_outbound` is an optional override for tests without an
    intent row.
    """
    from .relay import approve_intent, is_outbound as intent_outbound, suppress_intent

    packet_id = int(draft_ref)
    outbound = is_outbound if is_outbound is not None else intent_outbound(db_path, draft_ref)

    if action is ButtonAction.APPROVE:
        mark_answered(db_path, packet_id)
        if outbound:
            # Decision answered; flip the intent to 'approved'. Relay (separate,
            # sole credential-holder, R-D1) sends the frozen payload — not us.
            approve_intent(db_path, draft_ref)
            return ActionResult("✅ *Approved* — Relay sending…", enqueue_send=True)
        return ActionResult("✅ *Approved* — acknowledged (nothing to send).")

    if action is ButtonAction.MARK_SENT:
        # Josh sent it himself. Suppress the intent so Relay never fires (R-D4).
        mark_answered(db_path, packet_id)
        mark_completed(db_path, packet_id)
        suppress_intent(db_path, draft_ref)
        return ActionResult("📤 *Sent* — completed.")

    if action is ButtonAction.REJECT:
        suppress_intent(db_path, draft_ref)
        return ActionResult("❌ *Rejected* — dropped, nothing sent.")

    # REVISE
    return ActionResult("✍️ *Revise* — awaiting a redraft.")
