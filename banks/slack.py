"""Slack draft-delivery to #banks (BANKS-01 / B1.1 surface, wired in B4).

The only sanctioned egress. Posts a Draft into the private, Josh-only #banks
channel using a token scoped to that ONE channel. Refuses any other channel by
construction. If the token isn't provisioned yet (T2 pending), it writes the
draft to a local outbox so the pipeline is exercisable end-to-end today.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .config import BanksConfig, load_config
from .enforcement import Draft, Egress, assert_egress_allowed


class WrongChannel(RuntimeError):
    """Raised if anything tries to post outside the one sanctioned #banks channel."""


class BanksSlack:
    def __init__(self, config: BanksConfig | None = None) -> None:
        self.config = config or load_config()

    def post_draft(self, draft: Draft, channel_id: str | None = None) -> dict:
        """Post a draft to #banks — the ONLY egress Banks may perform.

        `channel_id` defaults to the configured #banks channel and may not be
        overridden to any other channel.
        """
        # Gate 1: drafts-only enforcement. This is the single allowed action.
        assert_egress_allowed(Egress.POST_DRAFT_TO_BANKS_CHANNEL)

        target = channel_id or self.config.slack_channel_id
        # Gate 2: channel lock. Never post anywhere but the configured #banks.
        if self.config.slack_channel_id and target != self.config.slack_channel_id:
            raise WrongChannel(
                f"Banks may only post to its #banks channel "
                f"({self.config.slack_channel_id}); refused target {target!r}."
            )

        message = draft.as_channel_message()

        if not self.config.slack_ready:
            # T2 not provisioned yet: land the draft in the local outbox.
            return self._write_outbox(draft, message)

        return self._post_to_slack(target, message)

    def _post_to_slack(self, channel: str, text: str) -> dict:
        # Kept import-local so the package has no hard runtime dependency
        # until a real token exists.
        from slack_sdk import WebClient  # type: ignore

        client = WebClient(token=self.config.slack_bot_token)
        resp = client.chat_postMessage(channel=channel, text=text)
        return {"ok": bool(resp.get("ok")), "channel": channel, "ts": resp.get("ts")}

    def _write_outbox(self, draft: Draft, message: str) -> dict:
        os.makedirs(self.config.outbox_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = os.path.join(self.config.outbox_dir, f"{ts}-{draft.kind}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "channel": "#banks (outbox — token pending T2)",
                    "kind": draft.kind,
                    "subject": draft.subject,
                    "rendered": message,
                    "detailed_financial": draft.detailed_financial,
                },
                fh,
                indent=2,
            )
        return {"ok": True, "outbox_path": path, "slack_ready": False}
