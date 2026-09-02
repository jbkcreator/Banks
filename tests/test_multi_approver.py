"""BANKS_APPROVER_USER_ID accepts several ids.

Approve triggers a real Relay send, so each id in the list is a genuine
authority grant — these tests pin that the list is honoured exactly and that
nobody outside it slips through.
"""
from __future__ import annotations

from banks.config import BanksConfig
from banks.socket_listener import is_authorized

# Placeholder ids — the real ones live in BANKS_APPROVER_USER_ID, not in tests.
JOSH = "U_APPROVER_ONE"
HARI = "U_APPROVER_TWO"


def _cfg(approvers):
    return BanksConfig(slack_bot_token=None, slack_channel_id=None,
                       approver_user_id=approvers)


def test_both_configured_approvers_are_authorized():
    cfg = _cfg(f"{JOSH},{HARI}")
    assert is_authorized(cfg, JOSH)
    assert is_authorized(cfg, HARI)


def test_a_stranger_is_never_authorized():
    cfg = _cfg(f"{JOSH},{HARI}")
    assert not is_authorized(cfg, "U_SOMEONE_ELSE")
    assert not is_authorized(cfg, "")


def test_whitespace_and_trailing_commas_are_tolerated():
    assert _cfg(f" {JOSH} , {HARI} ,").approver_ids == (JOSH, HARI)


def test_single_id_still_works():
    cfg = _cfg(JOSH)
    assert cfg.approver_ids == (JOSH,)
    assert is_authorized(cfg, JOSH)
    assert not is_authorized(cfg, HARI)


def test_unset_allows_anyone_for_test_workspaces():
    """Empty means open — run() refuses to start live in this state."""
    for value in (None, "", "  ", ","):
        cfg = _cfg(value)
        assert cfg.approver_ids == ()
        assert is_authorized(cfg, "U_ANYONE")


def test_ids_are_matched_exactly_not_by_prefix():
    """A substring must not authorize — ids are compared whole."""
    cfg = _cfg(JOSH)
    assert not is_authorized(cfg, JOSH[:-1])
    assert not is_authorized(cfg, JOSH + "X")
