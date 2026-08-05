"""Emoji reaction fallback poller (belt-and-suspenders for the button loop).

Buttons via Socket Mode are primary (E-D3). If that listener is down, a click
errors — so Josh can still react with an emoji and this poller catches it on
the next tick. Stateless: reads channel history, finds Banks' own draft
messages (block_id `approve::<draft_ref>`), maps any reaction to a ButtonAction,
and applies it. apply_action is idempotent, so re-seeing a reaction is safe.
"""

from __future__ import annotations

from .approval import ButtonAction, apply_action

# A-D8 vocabulary as emoji names (Slack `reactions` use names, not glyphs).
EMOJI_TO_ACTION = {
    "white_check_mark": ButtonAction.APPROVE,
    "heavy_check_mark": ButtonAction.APPROVE,
    "outbox_tray": ButtonAction.MARK_SENT,
    "x": ButtonAction.REJECT,
    "no_entry_sign": ButtonAction.REJECT,
    "pencil2": ButtonAction.REVISE,
    "writing_hand": ButtonAction.REVISE,
}


def draft_ref_of(message: dict) -> str | None:
    """Extract draft_ref from a Banks draft message's actions block_id."""
    for block in message.get("blocks", []) or []:
        bid = block.get("block_id", "")
        if bid.startswith("approve::"):
            return bid.split("approve::", 1)[1]
    return None


def poll_once(db_path: str, web, channel_id: str, limit: int = 50) -> list[tuple[str, ButtonAction, str]]:
    """One poll pass. Returns (draft_ref, action, user) applied. Idempotent."""
    applied: list[tuple[str, ButtonAction, str]] = []
    history = web.conversations_history(channel=channel_id, limit=limit)
    for msg in history.get("messages", []):
        draft_ref = draft_ref_of(msg)
        if not draft_ref:
            continue
        for reaction in msg.get("reactions", []) or []:
            action = EMOJI_TO_ACTION.get(reaction.get("name"))
            if not action:
                continue
            users = reaction.get("users", []) or [""]
            apply_action(db_path, action, draft_ref, users[0])
            applied.append((draft_ref, action, users[0]))
    return applied
