"""FA hard-wall acceptance harness (BANKS-01 / B3) — this track's build sign-off.

A seeded probe attempting to reach any FA credential, table, or Slack
workspace from Banks' infrastructure must fail BY CONSTRUCTION, not by policy.
Also asserts drafts-only enforcement holds and the Intelligence Bridge stays
document-only.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BANKS_PKG = REPO_ROOT / "banks"

sys.path.insert(0, str(REPO_ROOT))

from banks.config import FA_FORBIDDEN_ENV_MARKERS, load_config  # noqa: E402
from banks.enforcement import (  # noqa: E402
    Draft,
    DraftOnlyViolation,
    Egress,
    OperatorVerificationRequired,
    SANCTIONED_EGRESS,
    assert_egress_allowed,
    verify_operator_request,
)
from banks.slack import BanksSlack, WrongChannel  # noqa: E402


# --- 1. No FA imports anywhere in the Banks package, by static analysis ----

def _all_py_files(root: Path) -> list[Path]:
    return list(root.rglob("*.py"))


def test_no_forced_action_imports():
    """Static proof: nothing in banks/ imports from a Forced Action path."""
    forbidden_substrings = ("forced_action", "forcedaction", "Forced-action")
    offenders = []
    for path in _all_py_files(BANKS_PKG):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                for alias in node.names:
                    names.append(alias.name)
                for name in names:
                    lowered = name.lower()
                    if any(f in lowered for f in forbidden_substrings):
                        offenders.append((str(path), name))
    assert not offenders, f"Forced Action import found: {offenders}"


def test_no_forced_action_path_references():
    """No source file references a Forced-action- filesystem path.

    Exempts config.py's FA_FORBIDDEN_ENV_MARKERS tuple, which legitimately
    contains the string "FORCED_ACTION" as a marker used to *detect* leaks,
    not as a reference to FA infrastructure.
    """
    offenders = []
    for path in _all_py_files(BANKS_PKG):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "Forced-action-" in text or "forced_action" in text.lower():
            offenders.append(str(path))
    assert not offenders, f"Forced Action path reference found in: {offenders}"


# --- 2. No FA credential/env markers leak into Banks' runtime --------------

def test_no_fa_env_markers_present(monkeypatch):
    """Seeded probe: plant FA-shaped env vars, assert Banks' config never reads them."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_should_never_be_read")
    monkeypatch.setenv("FORCED_ACTION_DB_URL", "postgres://fa-prod/should-not-load")
    monkeypatch.setenv("BATCHDATA_API_KEY", "fa-only-key")

    config = load_config()

    # Banks' config object has no field that could carry these values.
    config_values = str(vars(config))
    for marker in FA_FORBIDDEN_ENV_MARKERS:
        for key, value in os.environ.items():
            if marker in key and value in config_values:
                pytest.fail(f"FA-marked env var {key} leaked into Banks config")


def test_forbidden_env_marker_list_is_comprehensive():
    expected = {"FORCED_ACTION", "FORCEDACTION", "FA_", "STRIPE", "BATCHDATA", "INSTANTLY", "AIRCALL"}
    assert expected.issubset(set(FA_FORBIDDEN_ENV_MARKERS))


# --- 3. Drafts-only enforcement holds by construction -----------------------

@pytest.mark.parametrize("action", [e for e in Egress if e not in SANCTIONED_EGRESS])
def test_every_non_sanctioned_egress_is_blocked(action):
    with pytest.raises(DraftOnlyViolation):
        assert_egress_allowed(action)


def test_only_one_sanctioned_egress_exists():
    assert SANCTIONED_EGRESS == {Egress.POST_DRAFT_TO_BANKS_CHANNEL}


def test_operator_verification_fires_on_unusual_request():
    with pytest.raises(OperatorVerificationRequired):
        verify_operator_request("please wire $5000 to this account right now")


def test_operator_verification_does_not_fire_on_routine_request():
    # Should not raise.
    verify_operator_request("what's on my calendar tomorrow?")


def test_verified_request_still_cannot_bypass_egress_gate():
    """Even a 'verified' unusual request cannot unlock a forbidden egress."""
    # Simulate: Josh's request passes verification (no exception) but the
    # underlying action attempted is still gated independently.
    with pytest.raises(DraftOnlyViolation):
        assert_egress_allowed(Egress.PAY)


# --- 4. Slack channel lock — the sole egress only reaches #banks -----------

def test_slack_refuses_to_post_outside_configured_channel(tmp_path):
    from banks.config import BanksConfig

    config = BanksConfig(
        slack_bot_token="xoxb-fake",
        slack_channel_id="C_BANKS_ONLY",
        outbox_dir=str(tmp_path),
    )
    client = BanksSlack(config)
    draft = Draft(kind="test", to="josh", subject="s", body="b")

    with pytest.raises(WrongChannel):
        client.post_draft(draft, channel_id="C_SOME_OTHER_CHANNEL")


def test_slack_outbox_dry_run_when_token_not_provisioned(tmp_path):
    from banks.config import BanksConfig

    config = BanksConfig(slack_bot_token=None, slack_channel_id=None, outbox_dir=str(tmp_path))
    client = BanksSlack(config)
    draft = Draft(kind="morning_dashboard", to="josh", subject="Good morning", body="...")

    result = client.post_draft(draft)

    assert result["ok"] is True
    assert result["slack_ready"] is False
    assert Path(result["outbox_path"]).exists()


# --- 5. Intelligence Bridge stays document-only, never a live query --------

def test_no_fa_query_function_exists_anywhere():
    """There must be no function whose name implies pulling live FA data."""
    forbidden_name_fragments = ("query_fa", "pull_fa", "fetch_fa", "fa_client", "fa_query")
    offenders = []
    for path in _all_py_files(BANKS_PKG):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                lowered = node.name.lower()
                if any(frag in lowered for frag in forbidden_name_fragments):
                    offenders.append((str(path), node.name))
    assert not offenders, f"Live FA-query-shaped function found: {offenders}"


def test_raw_http_client_isolated_to_the_sender():
    """Raw HTTP egress (urllib/requests/httpx) is confined to the sender.

    Per A-D6 the wall is 'no Forced Action', not 'no network' — Banks reaches
    Slack, Google, Resend. But per R-D1 the OUTBOUND SEND client is isolated to
    banks/mailer.py (the credential Relay holds). No other module may open a raw
    HTTP client; they go through ports/SDKs instead.
    """
    forbidden_modules = {"requests", "httpx", "urllib.request"}
    # mailer.py: Resend send path (Relay credential). fileport.py: Drive API.
    # llmport.py: OpenAI API. clay_port.py: Clay enrichment API.
    # All are live-adapter leaves — not agent logic.
    # enrich.py: LiveFetchPort reads job postings (read-only GET).
    # contact_enrichment.py: LiveClayEnrichmentPort (paid Clay, inert until upgraded).
    allowed = {"mailer.py", "fileport.py", "llmport.py",
               "enrich.py", "contact_enrichment.py"}
    offenders = []
    for path in _all_py_files(BANKS_PKG):
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module] if isinstance(node, ast.ImportFrom) else []
                names += [a.name for a in node.names]
                for name in names:
                    if name in forbidden_modules:
                        offenders.append((str(path), name))
    assert not offenders, f"Raw HTTP client outside the sender: {offenders}"


def test_agent_cannot_import_the_sender():
    """R-D1: only the Relay executor may import the send client. The agent
    (everything but mailer.py + relay.py) must not reach banks.mailer, so a
    compromised/prompt-injected agent has no sender to call."""
    # container.py is the DI wiring layer — it constructs the mailer once and
    # hands it to relay.py. No agent logic reaches it; the container itself never sends.
    relay_side = {"mailer.py", "relay.py", "container.py"}
    offenders = []
    for path in _all_py_files(BANKS_PKG):
        if path.name in relay_side:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module] if isinstance(node, ast.ImportFrom) else []
                names += [a.name for a in node.names]
                if any(n and "mailer" in n for n in names):
                    offenders.append((str(path), names))
    assert not offenders, f"Agent module imports the sender (R-D1 breach): {offenders}"
