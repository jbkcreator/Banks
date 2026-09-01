"""ChatPort — the one Slack surface (E-D1/E-D3), Fake + Live behind a seam.

Unifies the pieces proven live against the test workspace: post a Block Kit
draft with approval buttons (approval.render_draft_blocks), post the morning
brief (briefing.render_brief_blocks), and update a message in place after a
click. Live posts go through the SAME two gates as banks.slack.BanksSlack —
drafts-only egress + the #banks channel lock — so nothing here widens the wall.

Fake captures posts in memory for tests; Live wraps slack_sdk. Domain code
depends on the ChatPort protocol, never on Slack directly.
"""

from __future__ import annotations

from typing import Protocol

from .approval import render_draft_blocks
from .config import BanksConfig, load_config
from .enforcement import Draft, Egress, assert_egress_allowed
from .slack import WrongChannel


class ChatPort(Protocol):
    def post_draft(self, draft: Draft, draft_ref: str,
                   thread_ts: str | None = None) -> dict: ...
    def post_blocks(self, text: str, blocks: list[dict],
                    thread_ts: str | None = None) -> dict: ...
    def update(self, ts: str, text: str, blocks: list[dict]) -> dict: ...


class FakeChatPort:
    """In-memory ChatPort for tests — records everything, sends nothing."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self._ts = 0

    def post_draft(self, draft: Draft, draft_ref: str,
                   thread_ts: str | None = None) -> dict:
        assert_egress_allowed(Egress.POST_DRAFT_TO_BANKS_CHANNEL)
        return self.post_blocks(
            f"[DRAFT — {draft.kind}] {draft.subject}",
            render_draft_blocks(draft, draft_ref),
            thread_ts=thread_ts,
        )

    def post_blocks(self, text: str, blocks: list[dict],
                    thread_ts: str | None = None) -> dict:
        self._ts += 1
        ts = f"{self._ts:.6f}"
        self.posts.append({"ts": ts, "text": text, "blocks": blocks,
                           "thread_ts": thread_ts})
        return {"ok": True, "ts": ts}

    def update(self, ts: str, text: str, blocks: list[dict]) -> dict:
        self.updates.append({"ts": ts, "text": text, "blocks": blocks})
        return {"ok": True, "ts": ts}


class LiveChatPort:
    """Real Slack ChatPort. Same egress + channel-lock gates as BanksSlack."""

    def __init__(self, config: BanksConfig | None = None) -> None:
        self.config = config or load_config()

    def _client(self):
        from slack_sdk.web import WebClient
        return WebClient(token=self.config.slack_bot_token)

    def post_draft(self, draft: Draft, draft_ref: str,
                   thread_ts: str | None = None) -> dict:
        assert_egress_allowed(Egress.POST_DRAFT_TO_BANKS_CHANNEL)
        return self.post_blocks(
            f"[DRAFT — {draft.kind}] {draft.subject}",
            render_draft_blocks(draft, draft_ref),
            thread_ts=thread_ts,
        )

    def post_blocks(self, text: str, blocks: list[dict],
                    thread_ts: str | None = None) -> dict:
        # Gate: drafts-only egress. Posting to #banks is the one sanctioned act.
        assert_egress_allowed(Egress.POST_DRAFT_TO_BANKS_CHANNEL)
        channel = self.config.slack_channel_id
        if not channel:
            raise WrongChannel("No #banks channel configured; refusing to post.")
        # Suppress link/media unfurling — receipts carry job-posting + Gmail
        # links; Slack's auto-preview cards (esp. a generic "Gmail is email…"
        # card) are noise that clutters the channel.
        kwargs = {"channel": channel, "text": text, "blocks": blocks,
                  "unfurl_links": False, "unfurl_media": False}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        resp = self._client().chat_postMessage(**kwargs)
        return {"ok": bool(resp.get("ok")), "ts": resp.get("ts"), "channel": channel}

    def update(self, ts: str, text: str, blocks: list[dict]) -> dict:
        channel = self.config.slack_channel_id
        if not channel:
            raise WrongChannel("No #banks channel configured; refusing to update.")
        resp = self._client().chat_update(channel=channel, ts=ts, text=text, blocks=blocks)
        return {"ok": bool(resp.get("ok")), "ts": resp.get("ts")}
