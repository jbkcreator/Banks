"""Kill-switch regression: halt must fire on ANY human message, incl. edits.

Before the fix, `_handle_message` returned on `subtype`/`bot_id` BEFORE the halt
check, so a "stop all" typed as an edited message (subtype 'message_changed')
was silently dropped — a shadowed kill switch. These tests pin that halt is
evaluated on the broadest surface, and that the bot's own message never halts.
"""
from __future__ import annotations

import dataclasses

import pytest

from banks.config import load_config
from banks.halt import clear_halt, is_halted
from banks.socket_listener import _handle_message


class _FakeWeb:
    def __init__(self):
        self.posts = []

    def chat_postMessage(self, **kw):
        self.posts.append(kw)
        return {"ok": True}


@pytest.fixture(autouse=True)
def _reset_halt():
    clear_halt()
    yield
    clear_halt()


def _cfg():
    return dataclasses.replace(load_config(), db_path=":memory:", slack_channel_id="C0TEST")


def test_halt_on_plain_message():
    _handle_message(_cfg(), _FakeWeb(), llm=None, chat=None,
                    event={"type": "message", "text": "stop all", "user": "U1"})
    assert is_halted()


def test_halt_on_edited_message():
    # Edited message: text lives under `message`, subtype set. Must still halt.
    ev = {"type": "message", "subtype": "message_changed",
          "message": {"text": "stop banks", "user": "U1"}}
    _handle_message(_cfg(), _FakeWeb(), llm=None, chat=None, event=ev)
    assert is_halted()


def test_bot_message_never_halts():
    ev = {"type": "message", "text": "stop all", "bot_id": "B1"}
    _handle_message(_cfg(), _FakeWeb(), llm=None, chat=None, event=ev)
    assert not is_halted()


def test_non_halt_edit_is_ignored_not_crash():
    # A random edit (not a halt phrase) must be ignored quietly, not processed.
    ev = {"type": "message", "subtype": "message_changed",
          "message": {"text": "nice thanks"}}
    _handle_message(_cfg(), _FakeWeb(), llm=None, chat=None, event=ev)
    assert not is_halted()
