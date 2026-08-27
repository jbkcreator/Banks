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
from .config import BanksConfig, load_config
from .halt import is_halt_command, set_halt


def is_authorized(cfg: BanksConfig, user_id: str) -> bool:
    """Single-approver lock (Q13): only Josh's id may drive actions.

    Approve triggers a real Relay send — that authority is Josh's alone. A None
    approver_user_id means "allow anyone" (test workspaces where Lesly is the
    only member).
    """
    return cfg.approver_user_id is None or user_id == cfg.approver_user_id


def classify_incoming(text: str, has_pending_thread: bool) -> str:
    """Listener precedence (Q23), pure so it's unit-testable without Slack.

    (1) halt/kill ALWAYS first — a shadowed kill switch is a broken kill switch.
    (2) a reply on a pending draft thread → revise.
    (3) any other non-empty top-level message → command.
    (4) otherwise ignore.
    """
    if is_halt_command(text or ""):
        return "halt"
    if has_pending_thread:
        return "revise"
    if (text or "").strip():
        return "command"
    return "ignore"


def _handle_action(cfg: BanksConfig, web, payload: dict) -> None:
    actions = payload.get("actions") or []
    if not actions:
        return
    act = actions[0]
    try:
        button = ButtonAction(act.get("action_id"))
    except ValueError:
        return  # not one of ours
    draft_ref = act.get("value")
    user_id = (payload.get("user") or {}).get("id", "")
    channel = (payload.get("channel") or {}).get("id")
    ts = (payload.get("message") or {}).get("ts")

    # Single-approver lock: ignore clicks from anyone but Josh (Q13).
    if not is_authorized(cfg, user_id):
        return

    # is_outbound is read from the draft's send_intent (R-D3), set at draft time
    # by propose(). Approve on an email:* intent enqueues Relay; none:internal
    # just acknowledges.
    result = apply_action(cfg.db_path, button, draft_ref, user_id)

    if channel and ts:
        web.chat_update(
            channel=channel,
            ts=ts,
            text=result.status_text,
            blocks=[
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": result.status_text}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn",
                               "text": f"draft_ref `{draft_ref}` · by <@{user_id}>"}]},
            ],
        )


def run(cfg: BanksConfig | None = None) -> None:
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient

    cfg = cfg or load_config()
    if not (cfg.slack_bot_token and cfg.slack_app_token):
        raise SystemExit("Need BANKS_SLACK_BOT_TOKEN and BANKS_SLACK_APP_TOKEN.")

    web = WebClient(token=cfg.slack_bot_token)
    sm = SocketModeClient(app_token=cfg.slack_app_token, web_client=web)

    def _on(client: SocketModeClient, req: SocketModeRequest) -> None:
        # Ack first (Slack requires a prompt ack), then act.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        # Kill command (T3-14): handle plain messages before button dispatch.
        if req.type == "events_api":
            event = (req.payload.get("event") or {})
            text = event.get("text") or ""
            if is_halt_command(text):
                set_halt(reason=f"operator command: '{text.strip()}'")
                web.chat_postMessage(
                    channel=cfg.slack_channel_id or "",
                    text="🛑 *Banks halted.* All jobs suspended. Restart to resume.",
                )
                return

        if req.type == "interactive":
            try:
                _handle_action(cfg, web, req.payload)
            except Exception as exc:  # never let a click die silently
                print(f"[listener] handler error: {exc!r}", flush=True)

    sm.socket_mode_request_listeners.append(_on)
    sm.connect()
    print("Banks Socket Mode listener connected. Waiting for button clicks… Ctrl-C to stop.")
    from threading import Event
    Event().wait()


if __name__ == "__main__":
    run()
