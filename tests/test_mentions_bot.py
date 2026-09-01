"""Unit tests for _mentions_bot — the helper that detects @banks mentions in events.

Slack file-share messages sometimes put the mention only in blocks (rich text),
not in the text field.  _mentions_bot must handle all four cases:
  1. Mention in text as <@UID>
  2. Mention in text as <@UID|display> variant
  3. Mention only in blocks (rich_text user element) — the file-share case
  4. No mention anywhere → False
"""
from __future__ import annotations

from banks.socket_listener import _mentions_bot

BOT = "U0BN4F05R0S"


def _blocks_with_user(user_id: str) -> list:
    return [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "user", "user_id": user_id},
                        {"type": "text", "text": " here is my JD"},
                    ],
                }
            ],
        }
    ]


def test_plain_text_mention():
    event = {"text": f"<@{BOT}> review this", "blocks": []}
    assert _mentions_bot(event, BOT) is True


def test_plain_text_mention_with_display_name():
    event = {"text": f"<@{BOT}|banks> review this", "blocks": []}
    assert _mentions_bot(event, BOT) is True


def test_blocks_only_mention():
    # text is empty (or missing the markup) — mention lives only in blocks
    event = {"text": "", "blocks": _blocks_with_user(BOT)}
    assert _mentions_bot(event, BOT) is True


def test_blocks_mention_different_user():
    event = {"text": "", "blocks": _blocks_with_user("UOTHER")}
    assert _mentions_bot(event, BOT) is False


def test_no_mention_anywhere():
    event = {"text": "just a message", "blocks": []}
    assert _mentions_bot(event, BOT) is False


def test_empty_event():
    assert _mentions_bot({}, BOT) is False


def test_no_bot_user_id_plain_text():
    # When bot_user_id is unknown, fall back to starts-with <@
    event = {"text": "<@UANYONE> command", "blocks": []}
    assert _mentions_bot(event, "") is True


def test_no_bot_user_id_no_mention():
    event = {"text": "no mention here", "blocks": []}
    assert _mentions_bot(event, "") is False
