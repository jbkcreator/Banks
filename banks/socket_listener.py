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

    # TODO(R-D3): look up the draft's send_channel to set is_outbound precisely.
    # Until send_channel is persisted, treat clicks as non-outbound (acknowledge
    # only) so nothing is ever handed to a sender before Relay/Resend exist.
    result = apply_action(
        cfg.db_path, button, draft_ref, user_id, is_outbound=False
    )

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
