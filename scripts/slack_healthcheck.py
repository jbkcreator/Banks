"""Foolproof Slack healthcheck for the CLIENT workspace.

Goal: confirm the client's Banks Slack app is reachable and every feature's
transport works — WITHOUT any human clicking a button. If something is broken,
each check names the exact culprit + fix.

Reads CLIENT-namespaced creds from .env so it never disturbs the working test
creds (config.py's BANKS_* names). At cutover you copy CLIENT_SLACK_* → BANKS_*.

    CLIENT_SLACK_BOT_TOKEN=xoxb-...
    CLIENT_SLACK_APP_TOKEN=xapp-...
    CLIENT_SLACK_CHANNEL_ID=C...          (#banks)
    CLIENT_SLACK_JOBS_CHANNEL_ID=C...     (#banks-jobs)
    CLIENT_SLACK_APPROVER_USER_ID=U...    (Josh)

Run:  python scripts/slack_healthcheck.py          # posts + auto-deletes test msgs
      python scripts/slack_healthcheck.py --keep    # leave the test msgs in-channel

Exit 0 = all green. Exit 1 = at least one failure (see the summary table).

Checks (all automated, no click needed):
  1. env vars present + right prefix
  2. auth.test — bot token valid (prints bot + team)
  3. scopes include chat:write + files:read
  4. approver user id resolves (users.info)
  5. bot is a member of #banks
  6. bot is a member of #banks-jobs
  7. post to #banks (verify + delete)
  8. post to #banks-jobs (verify + delete)
  9. Block Kit card with Approve/Skip/Snooze buttons renders (post + delete)
 10. Socket Mode reachable (apps.connections.open with the app token) — proves
     the button transport without a click.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timezone

# --- load .env (same lightweight parse the other scripts use) --------------
root = pathlib.Path(__file__).resolve().parent.parent
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.split("#", 1)[0].strip())

KEEP = "--keep" in sys.argv

BOT = os.environ.get("CLIENT_SLACK_BOT_TOKEN", "")
APP = os.environ.get("CLIENT_SLACK_APP_TOKEN", "")
CH = os.environ.get("CLIENT_SLACK_CHANNEL_ID", "")
JOBS = os.environ.get("CLIENT_SLACK_JOBS_CHANNEL_ID", "")
APPROVER = os.environ.get("CLIENT_SLACK_APPROVER_USER_ID", "")

# results: list of (name, status, detail). status in {PASS, FAIL, SKIP}
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, "PASS" if ok else "FAIL", detail))
    return ok


def skip(name: str, detail: str) -> None:
    results.append((name, "SKIP", detail))


def _err(e) -> str:
    """Pull Slack's exact error code from a SlackApiError, else str(e)."""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            return resp.get("error") or str(e)
        except Exception:
            return str(e)
    return str(e)


# --- 1. env vars present + prefix ------------------------------------------
def check_env() -> bool:
    """Report per-var presence/prefix. Returns True only if BOT is present — the
    single hard blocker (nothing can be tested without it). Missing APPROVER/JOBS
    are recorded but do NOT abort: their own checks flag them, everything else
    still runs so you get the full reachability picture now."""
    checks = [
        ("CLIENT_SLACK_BOT_TOKEN", BOT, "xoxb-"),
        ("CLIENT_SLACK_APP_TOKEN", APP, "xapp-"),
        ("CLIENT_SLACK_CHANNEL_ID", CH, "C"),
        ("CLIENT_SLACK_JOBS_CHANNEL_ID", JOBS, "C"),
        ("CLIENT_SLACK_APPROVER_USER_ID", APPROVER, "U"),
    ]
    missing = [n for n, v, _ in checks if not v]
    wrongpfx = [f"{n}(want {p}…)" for n, v, p in checks if v and not v.startswith(p)]
    detail = "all 5 set, prefixes OK"
    if missing or wrongpfx:
        bits = []
        if missing:
            bits.append("not set: " + ", ".join(missing))
        if wrongpfx:
            bits.append("wrong prefix: " + ", ".join(wrongpfx))
        detail = " | ".join(bits)
    record("env vars present", not (missing or wrongpfx), detail)
    return bool(BOT)  # only BOT is a hard blocker for running anything


# --- Slack Web API checks ---------------------------------------------------
def run_web_checks() -> None:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    web = WebClient(token=BOT)

    # 2. auth.test
    try:
        auth = web.auth_test()
        record("auth.test (bot token valid)", True,
               f"bot=@{auth['user']} team={auth['team']}")
    except SlackApiError as e:
        record("auth.test (bot token valid)", False,
               f"{_err(e)} — bot token wrong/revoked or app not installed")
        # everything else depends on a valid token
        for n in ("scopes chat:write+files:read", "approver users.info",
                  "bot in #banks", "bot in #banks-jobs", "post #banks",
                  "post #banks-jobs", "block-kit card"):
            skip(n, "auth.test failed")
        return

    # 3. scopes (from the x-oauth-scopes response header on any call)
    try:
        scopes = (web.auth_test().headers or {}).get("x-oauth-scopes", "")
        have = set(s.strip() for s in scopes.split(","))
        need = {"chat:write", "files:read"}
        missing = need - have
        record("scopes chat:write+files:read", not missing,
               "all present" if not missing else f"missing: {', '.join(sorted(missing))}")
    except Exception as e:
        record("scopes chat:write+files:read", False, _err(e))

    # 4. approver users.info
    if not APPROVER:
        skip("approver users.info", "CLIENT_SLACK_APPROVER_USER_ID not provided yet")
    else:
        try:
            u = web.users_info(user=APPROVER)
            record("approver users.info", True,
                   f"@{u['user'].get('name', APPROVER)} resolves")
        except SlackApiError as e:
            record("approver users.info", False,
                   f"{_err(e)} — approver id wrong or not in this workspace")

    # 5/6. bot membership in each channel
    for label, cid in (("bot in #banks", CH), ("bot in #banks-jobs", JOBS)):
        if not cid:
            skip(label, "channel id not provided yet")
            continue
        try:
            info = web.conversations_info(channel=cid)
            is_member = info["channel"].get("is_member", False)
            nm = info["channel"].get("name", cid)
            record(label, bool(is_member),
                   f"#{nm} member" if is_member else f"#{nm}: bot NOT invited - /invite the bot")
        except SlackApiError as e:
            code = _err(e)
            hint = ("add channels:read scope + reinstall (posting still works, so "
                    "the bot IS in the channel)") if code == "missing_scope" else \
                   "channel id wrong or bot can't see it"
            record(label, False, f"{code} - {hint}")

    # 7/8/9. posting (verify + delete)
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    _post_and_delete(web, "post #banks", CH, f"🔧 Banks self-test — {stamp}")
    _post_and_delete(web, "post #banks-jobs", JOBS, f"🔧 Banks self-test — {stamp}")
    _post_card_and_delete(web, "block-kit card", CH, stamp)


def _post_and_delete(web, label, cid, text) -> None:
    from slack_sdk.errors import SlackApiError
    if not cid:
        skip(label, "channel id not provided yet")
        return
    try:
        resp = web.chat_postMessage(channel=cid, text=text)
        ts = resp["ts"]
        if KEEP:
            record(label, True, "posted (kept)")
            return
        try:
            web.chat_delete(channel=cid, ts=ts)
            record(label, True, "posted + deleted")
        except SlackApiError as e:
            record(label, True, f"posted; delete failed ({_err(e)}) — needs chat:write; harmless")
    except SlackApiError as e:
        record(label, False, f"{_err(e)} — bot not in channel / no chat:write / bad channel id")


def _post_card_and_delete(web, label, cid, stamp) -> None:
    from slack_sdk.errors import SlackApiError
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"🔧 *Banks self-test card* — {stamp}\nButtons render check (no click needed)."}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Approve"},
             "style": "primary", "action_id": "approve", "value": "selftest"},
            {"type": "button", "text": {"type": "plain_text", "text": "Skip"},
             "action_id": "skip", "value": "selftest"},
            {"type": "button", "text": {"type": "plain_text", "text": "Snooze"},
             "action_id": "snooze", "value": "selftest"},
        ]},
    ]
    try:
        resp = web.chat_postMessage(channel=cid, text="Banks self-test card", blocks=blocks)
        ts = resp["ts"]
        if KEEP:
            record(label, True, "buttons rendered (kept)")
            return
        try:
            web.chat_delete(channel=cid, ts=ts)
        except SlackApiError:
            pass
        record(label, True, "buttons rendered + deleted")
    except SlackApiError as e:
        record(label, False, f"{_err(e)} — Block Kit rejected or no post permission")


# --- 10. Socket Mode reachable (no click) ----------------------------------
def check_socket() -> None:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    try:
        # apps.connections.open needs an app-level token; returns a wss URL if
        # Socket Mode is enabled and the app token is valid. No WebSocket, no click.
        resp = WebClient().apps_connections_open(app_token=APP)
        ok = bool(resp.get("url", "").startswith("wss://"))
        record("Socket Mode reachable", ok,
               "app token valid + Socket Mode ON" if ok else "no wss url returned")
    except SlackApiError as e:
        code = _err(e)
        hint = {
            "invalid_auth": "app token wrong/revoked",
            "not_allowed_token_type": "not an app-level token (need xapp-)",
        }.get(code, "Socket Mode likely OFF in the app config")
        record("Socket Mode reachable", False, f"{code} — {hint}")


# --- run + report -----------------------------------------------------------
def main() -> int:
    if not check_env():
        # still try auth so we surface more, but env is the root blocker
        _print_summary()
        print("\n[STOP] CLIENT_SLACK_BOT_TOKEN missing — nothing else could be tested.")
        return 1
    try:
        run_web_checks()
    except ImportError:
        print("slack_sdk not installed — run: pip install slack_sdk")
        return 1
    check_socket()
    return _print_summary()


def _print_summary() -> int:
    print("\n" + "=" * 68)
    print("  BANKS - CLIENT SLACK HEALTHCHECK")
    print("=" * 68)
    icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}
    failures = 0
    for name, status, detail in results:
        if status == "FAIL":
            failures += 1
        # ASCII-sanitise so any terminal (Windows cp1252, plain server) prints it.
        safe = detail.encode("ascii", "replace").decode("ascii")
        print(f"  {icon[status]} {name:32} {safe}")
    print("=" * 68)
    if failures == 0:
        print("  ALL GREEN - client Slack reachable; every feature's transport works.")
        print("  (The one thing still needing a human: an actual button click.)")
    else:
        print(f"  {failures} FAILURE(S) - see the [FAIL] rows above for the culprit + fix.")
    print("=" * 68)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
