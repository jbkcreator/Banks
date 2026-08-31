"""Socket Mode listener — receives Block Kit button clicks over a WebSocket.

The always-on side of the button-approval design (E-D3). Uses the app-level
token (xapp-) to open an OUTBOUND WebSocket to Slack, so interactivity works
with no public endpoint / firewall hole. On a click it applies the pure
transition (approval.apply_action) and updates the message in place so the
`#banks` thread reflects reality.

Run it (test workspace, needs the process to stay up):

    python -m banks.socket_listener

This is the one long-lived process Banks needs for buttons; in production it is
the Phase-G box's service. If it is down, a click errors in Slack — the emoji
reaction fallback (banks.reactions poller) still catches approvals later.
"""

from __future__ import annotations

from .approval import ButtonAction, apply_action
from .attack_queue import (
    QUEUE_ACTION_DONE,
    QUEUE_ACTION_SKIP,
    QUEUE_ACTION_SNOOZE,
)
from .commands import handle_command, route
from .config import BanksConfig, load_config
from .halt import clear_halt, is_halt_command, is_unhalt_command, set_halt
from .queue_actions import mark_done, skip_item, snooze_item
from .revisions import (
    apply_revision,
    clear_pending_revision,
    get_pending_revision,
    set_pending_revision,
)

_CANCEL_WORDS = {"cancel", "nevermind", "never mind", "stop revising"}


def is_authorized(cfg: BanksConfig, user_id: str) -> bool:
    """Single-approver lock (Q13): only Josh's id may drive actions.

    Approve triggers a real Relay send — that authority is Josh's alone. A None
    approver_user_id means "allow anyone" (test workspaces where Lesly is the
    only member).
    """
    return cfg.approver_user_id is None or user_id == cfg.approver_user_id


def classify_incoming(text: str, has_pending_revision: bool) -> str:
    """Listener precedence (Q23), pure so it's unit-testable without Slack.

    (1) halt/kill ALWAYS first — a shadowed kill switch is a broken kill switch.
    (2) if the user has a pending Revise (tapped the button) → revise; their
        next message is the instruction. Button-driven, since Slack one-level
        threading can't target a card by reply thread_ts.
    (3) any other non-empty message → command.
    (4) otherwise ignore.
    """
    if is_halt_command(text or ""):
        return "halt"
    if has_pending_revision:
        return "revise"
    if (text or "").strip():
        return "command"
    return "ignore"


def _handle_action(cfg: BanksConfig, web, payload: dict, llm=None, chat=None) -> None:
    actions = payload.get("actions") or []
    if not actions:
        return
    act = actions[0]
    action_id = act.get("action_id")
    draft_ref = act.get("value")
    user_id = (payload.get("user") or {}).get("id", "")
    channel = (payload.get("channel") or {}).get("id")
    ts = (payload.get("message") or {}).get("ts")

    # Single-approver lock: ignore clicks from anyone but Josh (Q13).
    if not is_authorized(cfg, user_id):
        print(f"[listener] IGNORED click action={action_id!r} from user={user_id!r} "
              f"(approver={cfg.approver_user_id!r}) — not the approver", flush=True)
        return
    print(f"[listener] click action={action_id!r} draft_ref={draft_ref!r} "
          f"by user={user_id!r}", flush=True)

    # Queue-only actions (Q3/Q4): Skip / Snooze / Mark-done live in queue_actions,
    # not approval.ButtonAction. Handle them before the ButtonAction parse.
    if action_id in (QUEUE_ACTION_SKIP, QUEUE_ACTION_SNOOZE, QUEUE_ACTION_DONE):
        status = _handle_queue_action(cfg, action_id, draft_ref)
        if channel and ts:
            _update_card(web, channel, ts, status, draft_ref, user_id)
        return

    try:
        button = ButtonAction(action_id)
    except ValueError:
        return  # not one of ours

    # Revise (Q4): the button identifies the exact card, so we capture the draft
    # in a per-user pending slot; the user's NEXT message is the instruction.
    # (Replaces the broken thread-reply targeting — Slack threads one level deep.)
    if button is ButtonAction.REVISE:
        set_pending_revision(cfg.db_path, user_id, draft_ref)
        if channel and ts:
            _update_card(
                web, channel, ts,
                "✍️ *Revising* — reply with your change (e.g. `shorter`, "
                "`less formal`, `stronger hook`). Say `cancel` to stop.",
                draft_ref, user_id,
            )
        return

    # is_outbound is read from the draft's send_intent (R-D3), set at draft time
    # by propose(). Approve on an email:* intent enqueues Relay; none:internal
    # just acknowledges.
    result = apply_action(cfg.db_path, button, draft_ref, user_id)
    print(f"[listener] {button.value} applied draft_ref={draft_ref!r} "
          f"enqueue_send={getattr(result, 'enqueue_send', None)}", flush=True)

    if button is ButtonAction.APPROVE:
        _maybe_generate_surround_pack(cfg, draft_ref, chat, llm)

    if channel and ts:
        _update_card(web, channel, ts, result.status_text, draft_ref, user_id)


def _maybe_generate_surround_pack(cfg: BanksConfig, draft_ref: str, chat, llm) -> None:
    """Approving a Tier A/B 'pursue this role?' card must produce its surround
    pack (MOD-03) — otherwise the click acknowledges the card and nothing else
    happens. Only 'opportunity' packets carry a linked opportunity (see
    opportunities.source_packet_id, set by intake._surface_opportunity); other
    kinds (property inquiries, outreach lanes themselves) are no-ops here.

    Idempotent: skipped if the opportunity already has any outreach_lanes rows,
    so a duplicate Slack delivery of the same click can't double-post lanes.
    """
    if chat is None:
        return
    from .opportunity import load_career_facts
    from .store import cursor
    from .surround import generate_surround_pack

    with cursor(cfg.db_path) as cur:
        opp = cur.execute(
            "SELECT id FROM opportunities WHERE source_packet_id = ?", (draft_ref,)
        ).fetchone()
        if not opp:
            return
        existing = cur.execute(
            "SELECT 1 FROM outreach_lanes WHERE opportunity_id = ? LIMIT 1",
            (opp["id"],),
        ).fetchone()
    if existing:
        return

    try:
        generate_surround_pack(cfg.db_path, opp["id"], load_career_facts(), chat, llm)
    except ValueError as exc:  # empty career-facts, missing opportunity — surface, don't crash
        print(f"[listener] surround pack error: {exc!r}", flush=True)


def _handle_queue_action(cfg: BanksConfig, action_id: str, draft_ref: str) -> str:
    """Skip / Snooze / Mark-done (Q3/Q4) → queue_actions; returns status text."""
    if action_id == QUEUE_ACTION_SKIP:
        skip_item(cfg.db_path, draft_ref)
        return "⏭️ *Skipped* — off today's queue (won't auto-return)."
    if action_id == QUEUE_ACTION_SNOOZE:
        snooze_item(cfg.db_path, draft_ref)  # default 1 day
        return "😴 *Snoozed* — back in tomorrow's queue."
    mark_done(cfg.db_path, draft_ref)
    return "✔️ *Marked done* — logged as a completed touch."


def _update_card(web, channel: str, ts: str, status_text: str,
                 draft_ref: str, user_id: str) -> None:
    web.chat_update(
        channel=channel, ts=ts, text=status_text,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": status_text}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                           "text": f"draft_ref `{draft_ref}` · by <@{user_id}>"}]},
        ],
    )


def _handle_message(cfg: BanksConfig, web, llm, chat, event: dict) -> None:
    """Dispatch a message by the ONE precedence rule: classify_incoming decides
    halt → revise → command → ignore, this function only acts on the verdict.
    (Previously the precedence was re-derived inline here + in _on; now the pure
    classifier is the single source of the rule and the live path exercises it.)
    """
    # Text can arrive top-level OR, for an EDITED message, under `message`
    # (subtype 'message_changed'). The kill switch must see both — a halt typed
    # as an edit is still a halt (a shadowed kill switch is a broken one).
    is_bot = bool(event.get("bot_id"))
    text = event.get("text") or (event.get("message") or {}).get("text") or ""
    user_id = event.get("user", "")
    channel = event.get("channel")

    # HALT FIRST, on the broadest surface — any human message including edits.
    # (Skip the bot's own messages so a halt-confirmation can't re-trigger halt.)
    if is_halt_command(text):
        if is_bot:
            return
        set_halt(reason=f"operator command: '{text.strip()}'")
        web.chat_postMessage(
            channel=cfg.slack_channel_id or "",
            text=("🛑 *Banks halted — ALL jobs suspended.* Nothing will send until "
                  "you say `resume`. (To stop just one company instead, say "
                  "`stop chasing <company>` or `replied <company>`.)"),
        )
        return

    # UN-HALT — approver-only (re-enabling sends must not be open to anyone).
    if is_unhalt_command(text):
        if is_bot:
            return
        if not is_authorized(cfg, user_id):
            print(f"[listener] IGNORED resume from user={user_id!r} "
                  f"(approver={cfg.approver_user_id!r})", flush=True)
            return
        clear_halt()
        web.chat_postMessage(
            channel=cfg.slack_channel_id or "",
            text="✅ *Banks resumed.* Standing jobs run again on the next tick.",
        )
        return

    # Non-halt: ignore the bot's own messages + non-plain subtypes (edits, joins,
    # etc.) — a revision/command loop otherwise is inevitable.
    if is_bot or event.get("subtype"):
        return

    # A pending Revise (Josh-only) captures this message as the instruction.
    pending = (get_pending_revision(cfg.db_path, user_id)
               if is_authorized(cfg, user_id) else None)

    verdict = classify_incoming(text, pending is not None)

    if verdict == "revise":
        if text.strip().lower() in _CANCEL_WORDS:
            clear_pending_revision(cfg.db_path, user_id)
            if channel:
                web.chat_postMessage(channel=channel, text="✍️ Revision cancelled.")
            return
        from .opportunity import load_career_facts
        res = apply_revision(cfg.db_path, pending, text, load_career_facts(), llm, chat)
        clear_pending_revision(cfg.db_path, user_id)
        if res.get("ok"):
            msg = f"✍️ Revised: {text.strip()}"
        elif res.get("reason") == "embellishment":
            msg = "✍️ Skipped — that would add a claim not in your resume."
        elif res.get("reason") == "no_pending_draft":
            msg = "✍️ No draft awaiting revision — tap *Revise* on a card first."
        else:
            msg = f"✍️ Couldn't revise: {res.get('reason')}"
        if channel:
            web.chat_postMessage(channel=channel, text=msg)
        return

    if verdict == "command":  # on-demand retrieval
        reply = handle_command(cfg.db_path, route(cfg.db_path, text, llm))
        if channel and reply:
            web.chat_postMessage(channel=channel, text=reply)
    # verdict == "ignore" → do nothing


# ---------------------------------------------------------------------------
# Slack CSV upload (MOD-01) — Josh drags a Simplify export into #banks-jobs.
# Requires the `files:read` scope on the Banks Slack app (CTO/client item).
# ---------------------------------------------------------------------------

def _is_csv(f: dict) -> bool:
    return (f.get("filetype") == "csv"
            or (f.get("name") or "").lower().endswith(".csv"))


def should_ingest_file(cfg: BanksConfig, event: dict) -> bool:
    """Pure gate: ingest a dropped file only if it's Josh's CSV in the jobs
    channel (single-approver + channel lock + skip bot files)."""
    if event.get("bot_id"):
        return False
    files = event.get("files") or []
    if not files or not any(_is_csv(f) for f in files):
        return False
    if not is_authorized(cfg, event.get("user", "")):
        return False
    if event.get("channel") != cfg.slack_jobs_channel_id:
        return False
    return True


def _handle_file(cfg: BanksConfig, web, chat, event: dict) -> None:
    """Download a dropped Simplify CSV and run intake; post a terse receipt."""
    import os
    import tempfile

    from .csvport import LiveCSVPort
    from .intake import ingest_simplify
    from .slackfiles import download

    channel = event.get("channel")
    files = event.get("files") or []
    # A file from Josh in the jobs channel that ISN'T a CSV → tell him.
    if (not event.get("bot_id")
            and is_authorized(cfg, event.get("user", ""))
            and channel == cfg.slack_jobs_channel_id
            and files and not any(_is_csv(f) for f in files)):
        web.chat_postMessage(channel=channel,
                             text="📎 That's not a CSV — drop a Simplify export (.csv).")
        return

    if not should_ingest_file(cfg, event):
        return

    for f in files:
        if not _is_csv(f):
            continue
        name = f.get("name") or "export.csv"
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            info = web.files_info(file=f.get("id"))
            url = (info.get("file") or {}).get("url_private_download")
        try:
            data = download(url, cfg.slack_bot_token or "")
            tmp = os.path.join(tempfile.mkdtemp(), name)
            with open(tmp, "wb") as fh:
                fh.write(data)
            res = ingest_simplify(cfg.db_path, LiveCSVPort(), tmp, chat)
        except Exception as exc:  # parse/download failure → clear message, no crash
            web.chat_postMessage(
                channel=channel,
                text=f"⚠️ Couldn't import *{name}* — is this a Simplify export? ({exc})",
            )
            continue
        total = res.ingested + res.duplicates + res.excluded
        web.chat_postMessage(
            channel=channel,
            text=(f"📥 Imported *{name}* — {total} rows: {res.ingested} new, "
                  f"{res.duplicates} duplicates, {res.excluded} excluded. "
                  f"{res.held} held for enrichment; Tier A/B appear in your queue "
                  f"as industry resolves."),
        )


def run(cfg: BanksConfig | None = None) -> None:
    cfg = cfg or load_config()
    if not (cfg.slack_bot_token and cfg.slack_app_token):
        raise SystemExit("Need BANKS_SLACK_BOT_TOKEN and BANKS_SLACK_APP_TOKEN.")
    if not cfg.approver_user_id:
        # Fail closed: an unset approver id makes is_authorized() allow anyone
        # (the test-workspace default). A live listener must never inherit that.
        raise SystemExit("Need BANKS_APPROVER_USER_ID set before running live — "
                         "an unset approver id lets any workspace member approve sends.")

    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient

    from .chatport import LiveChatPort
    from .llmport import load_llm_port

    web = WebClient(token=cfg.slack_bot_token)
    sm = SocketModeClient(app_token=cfg.slack_app_token, web_client=web)
    llm = load_llm_port()
    chat = LiveChatPort(cfg)

    def _on(client: SocketModeClient, req: SocketModeRequest) -> None:
        # Ack first (Slack requires a prompt ack), then act.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        import os as _os
        if _os.environ.get("BANKS_LISTENER_DEBUG"):
            _et = (req.payload.get("event") or {}).get("type") if isinstance(req.payload, dict) else None
            print(f"[listener] recv type={req.type} event={_et}", flush=True)

        if req.type == "events_api":
            event = (req.payload.get("event") or {})
            if event.get("type") != "message":
                return
            # A dropped file (Simplify CSV, MOD-01) → intake; otherwise the
            # halt→revise→command precedence in _handle_message.
            if event.get("files"):
                try:
                    _handle_file(cfg, web, chat, event)
                except Exception as exc:
                    print(f"[listener] file error: {exc!r}", flush=True)
                return
            try:
                _handle_message(cfg, web, llm, chat, event)
            except Exception as exc:
                print(f"[listener] message error: {exc!r}", flush=True)
            return

        if req.type == "interactive":
            try:
                _handle_action(cfg, web, req.payload, llm, chat)
            except Exception as exc:  # never let a click die silently
                print(f"[listener] handler error: {exc!r}", flush=True)

    sm.socket_mode_request_listeners.append(_on)
    sm.connect()
    print("Banks Socket Mode listener connected. Waiting for button clicks… Ctrl-C to stop.")
    from threading import Event
    Event().wait()


if __name__ == "__main__":
    run()
