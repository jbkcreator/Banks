"""DraftRef + SendChannel — architecture candidate 2.

These tests pin the two properties the refactor bought:
  * outbound-ness has ONE source of truth (the channel answers for itself)
  * a bad channel now fails loudly instead of silently becoming "not outbound"

That silent path was the bug: a typo made an outbound draft internal, so the
draft could be approved and would simply never send — the exact failure the
client named as his worst case.
"""

from __future__ import annotations

import pytest

from banks.refs import DraftRef, SendChannel


# --- SendChannel: one source of outbound truth -------------------------------

def test_email_channels_are_outbound_and_internal_is_not():
    assert SendChannel.PRAISE.is_outbound
    assert SendChannel.SENDAS.is_outbound
    assert not SendChannel.INTERNAL.is_outbound


def test_every_channel_answers_outbound_without_a_prefix_test():
    # No caller should ever need startswith("email:") again.
    for channel in SendChannel:
        assert isinstance(channel.is_outbound, bool)


def test_unknown_channel_raises_instead_of_silently_not_sending():
    # Previously: startswith("email:") on a typo → False → approved, never sent.
    with pytest.raises(ValueError, match="unknown send_channel"):
        SendChannel.parse("email:pariase")   # transposed typo
    with pytest.raises(ValueError, match="unknown send_channel"):
        SendChannel.parse("praise")          # missing prefix


def test_missing_channel_has_no_safe_default():
    with pytest.raises(ValueError, match="no safe default"):
        SendChannel.parse(None)


def test_parse_is_idempotent_and_accepts_stored_values():
    assert SendChannel.parse("email:praise") is SendChannel.PRAISE
    assert SendChannel.parse(SendChannel.PRAISE) is SendChannel.PRAISE


# --- DraftRef: a typed identity that still renders as the bare id ------------

def test_draft_ref_renders_as_bare_id_so_stored_rows_are_unchanged():
    # The refactor is a typing change, not a data change: Slack block_ids and
    # send_intents rows must stay byte-identical.
    assert str(DraftRef(7)) == "7"


def test_draft_ref_round_trips_from_every_boundary():
    assert DraftRef.parse("7") == DraftRef(7)      # Slack payload / DB column
    assert DraftRef.parse(7) == DraftRef(7)        # packet id
    assert DraftRef.parse(DraftRef(7)) == DraftRef(7)
    assert DraftRef.parse(" 7 ") == DraftRef(7)    # tolerant of padding


def test_draft_ref_rejects_non_ids():
    for bad in ("", "abc", "7.0", "approve::7"):
        with pytest.raises(ValueError):
            DraftRef.parse(bad)


def test_draft_ref_rejects_bool_despite_int_subclassing():
    with pytest.raises(TypeError):
        DraftRef.parse(True)


# --- The seam: relay reads outbound-ness through the enum --------------------

def test_relay_outbound_follows_the_stored_channel(tmp_path):
    from banks.relay import enqueue_intent, intent_channel, is_outbound
    from banks.store import init_db

    db = str(tmp_path / "t.db")
    init_db(db)

    enqueue_intent(db, DraftRef(1), SendChannel.PRAISE,
                   to_addr="praise@example.com", subject="s", body="b")
    enqueue_intent(db, DraftRef(2), SendChannel.INTERNAL,
                   to_addr=None, subject="s", body="b")

    assert intent_channel(db, DraftRef(1)) is SendChannel.PRAISE
    assert is_outbound(db, DraftRef(1)) is True
    assert is_outbound(db, DraftRef(2)) is False
    # no intent row at all → not outbound, but also nothing to send
    assert is_outbound(db, DraftRef(999)) is False


def test_enqueue_rejects_a_typo_channel_at_the_boundary(tmp_path):
    from banks.relay import enqueue_intent
    from banks.store import init_db

    db = str(tmp_path / "t.db")
    init_db(db)
    with pytest.raises(ValueError, match="unknown send_channel"):
        enqueue_intent(db, DraftRef(1), "email:pariase",
                       to_addr="x@example.com", subject="s", body="b")
