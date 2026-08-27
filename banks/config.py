"""Banks configuration — loaded from the personal secrets store only.

Never FA's 1Password Teams vault, never an FA env file. For local dev,
values come from a git-ignored `.env`/environment; in production they come
from Josh's personal secrets store (his choice — see client questions).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Substrings that would betray a leak of Forced Action credentials into
# Banks' environment. The hard-wall harness asserts none of these are set.
FA_FORBIDDEN_ENV_MARKERS = (
    "FORCED_ACTION",
    "FORCEDACTION",
    "FA_",
    "STRIPE",  # Banks never touches money / Stripe
    "BATCHDATA",
    "INSTANTLY",
    "AIRCALL",
)


@dataclass(frozen=True)
class BanksConfig:
    """Runtime config. `slack_channel_id` is the ONE channel Banks may post to."""

    slack_bot_token: str | None
    slack_channel_id: str | None
    # #banks-jobs channel for Daily Attack Queue (MOD-05). Confirmed: C0BNGMYHFEF
    slack_jobs_channel_id: str | None = None
    # App-level token (xapp-) for Socket Mode — receives button clicks over an
    # outbound WebSocket, no public endpoint. Distinct from the bot token.
    slack_app_token: str | None = None
    # Single-approver lock (MOD-05): only this Slack user id may drive actions
    # (Approve triggers a real Relay send — that authority is Josh's alone).
    # None = allow any user (test workspaces, where Lesly is the only member).
    approver_user_id: str | None = None
    timezone: str = "America/New_York"
    # Josh's own address — where detailed-financial drafts are emailed in full
    # (Slack only ever carries the redacted summary).
    josh_email: str | None = None
    # Google Calendar (read-only, service account). CalendarPort live target.
    gcp_sa_key: str | None = None
    calendar_id: str | None = None
    clay_api_key: str | None = None
    # LLM key. Banks-namespaced ONLY — never the generic ANTHROPIC_API_KEY, so a
    # shared environment can't leak Forced Action's key into Banks (wall + billing).
    anthropic_api_key: str | None = None
    db_path: str = "banks.db"
    # Where drafts land when Slack isn't provisioned yet (T2 pending): a local
    # outbox so the whole pipeline is exercisable before the token exists.
    outbox_dir: str = "outbox"

    @property
    def slack_ready(self) -> bool:
        return bool(self.slack_bot_token and self.slack_channel_id)


def load_config() -> BanksConfig:
    return BanksConfig(
        slack_bot_token=os.environ.get("BANKS_SLACK_BOT_TOKEN"),
        slack_channel_id=os.environ.get("BANKS_CHANNEL_ID"),
        slack_jobs_channel_id=os.environ.get("BANKS_JOBS_CHANNEL_ID", "C0BNGMYHFEF"),
        slack_app_token=os.environ.get("BANKS_SLACK_APP_TOKEN"),
        approver_user_id=os.environ.get("BANKS_APPROVER_USER_ID"),
        josh_email=os.environ.get("BANKS_JOSH_EMAIL"),
        gcp_sa_key=os.environ.get("BANKS_GCP_SA_KEY"),
        calendar_id=os.environ.get("BANKS_CALENDAR_ID"),
        timezone=os.environ.get("BANKS_TIMEZONE", "America/New_York"),
        clay_api_key=os.environ.get("BANKS_CLAY_API_KEY"),
        anthropic_api_key=os.environ.get("BANKS_ANTHROPIC_API_KEY"),
        db_path=os.environ.get("BANKS_DB_PATH", "banks.db"),
        outbox_dir=os.environ.get("BANKS_OUTBOX_DIR", "outbox"),
    )
