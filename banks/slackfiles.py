"""Slack file download — a leaf adapter (like mailer/fileport).

Downloading a `url_private` file needs an authed GET with the bot token, which
the slack_sdk WebClient doesn't wrap. Kept in its own module so the raw HTTP
client stays confined to a leaf (hard-wall: raw HTTP only in adapters, never in
agent logic). Slack-scoped only — never Forced Action.
"""
from __future__ import annotations

import urllib.request


def download(url: str, bot_token: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {bot_token}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()
