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
    # Approver lock (MOD-05): only these Slack user ids may drive actions
    # (Approve triggers a real Relay send, so this is a real authority grant).
    # Accepts a comma-separated list — Josh plus whoever operates Banks with him.
    # None/empty = allow any user (test workspaces with a single member).
    # Read it through `approver_ids`, never by comparing this string directly.
    approver_user_id: str | None = None

    @property
    def approver_ids(self) -> tuple[str, ...]:
        """Every Slack user id allowed to drive actions ('U1, U2' -> ('U1','U2'))."""
        raw = self.approver_user_id or ""
        return tuple(p.strip() for p in raw.split(",") if p.strip())
    timezone: str = "America/New_York"
    # Josh's own address — where detailed-financial drafts are emailed in full
    # (Slack only ever carries the redacted summary).
    josh_email: str | None = None
    # Google Calendar (read-only, service account). CalendarPort live target.
    gcp_sa_key: str | None = None
    calendar_id: str | None = None
    clay_api_key: str | None = None
    # Clay paid integration (MOD-02). submit() POSTs queued companies into a Clay
    # table via its inbound webhook; Clay enriches async and writes rows to a
    # Google Sheet buffer, which retrieve() polls read-only (no inbound surface —
    # keeps the wall physical). Both blank → LiveClay stays inert (manual CSV path).
    clay_webhook_url: str | None = None
    enrichment_sheet_id: str | None = None
    enrichment_sheet_range: str = "Sheet1"
    # LLM key. Banks-namespaced ONLY — never the generic ANTHROPIC_API_KEY, so a
    # shared environment can't leak Forced Action's key into Banks (wall + billing).
    anthropic_api_key: str | None = None
    db_path: str = "banks.db"
    # Where drafts land when Slack isn't provisioned yet (T2 pending): a local
    # outbox so the whole pipeline is exercisable before the token exists.
    outbox_dir: str = "outbox"
    # MOD-06 exclusion seed file — source of truth for who Banks must never
    # contact. Loaded at startup; a Slack `exclude` command writes back here.
    exclusions_file: str = "exclusions.txt"
    # MOD-01 target watchlist (item 6): Josh's priority companies. A posted role
    # at a listed company gets a graded fit-score bump (targets.py). Loaded at
    # startup like exclusions; a passive score boost, never proactive surfacing.
    targets_file: str = "targets.txt"
    # Outbound SMTP (Relay's mailer). Banks' OWN separate mailbox — never FA's.
    # STARTTLS on 587. from_addr is Josh's sending identity for outreach.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    # Resend API key — the alternative outbound sender. Kept on config (not read
    # from os.environ at the call site) so ALL creds funnel through here, which is
    # what the hard-wall test guards.
    resend_api_key: str | None = None
    # MOD-01 forwarded email intake — dedicated banks-intake@gmail.com mailbox.
    # IMAP polled every 10 min; Josh forwards confirmation emails here.
    intake_email: str | None = None
    intake_email_password: str | None = None  # Gmail app password
    # No-Open-Role Lite (MOD-05): proactively pitch consulting to warm-contact
    # companies that have NOT posted a role. Client policy (2026-08-29): off —
    # consulting only when a real role/conversation points that way. Flag kept so
    # it's a one-line flip if Josh later wants warm-company pitches, not a rebuild.
    proactive_consulting_enabled: bool = False
    # Remote preference (client 2026-08-31: "can't do New York hybrid, just full
    # remote"). When on, a hybrid/onsite role OUTSIDE the home market (Tampa/FL)
    # gets a heavy fit penalty so it sinks to Tier C. Remote + home-market local
    # are untouched. On by default per Josh's stated constraint; a config flip.
    remote_only_roles: bool = True

    @property
    def slack_ready(self) -> bool:
        return bool(self.slack_bot_token and self.slack_channel_id)


def load_config() -> BanksConfig:
    return BanksConfig(
        slack_bot_token=os.environ.get("BANKS_SLACK_BOT_TOKEN"),
        slack_channel_id=os.environ.get("BANKS_CHANNEL_ID"),
        # No default: this used to fall back to a hard-coded TEST-workspace
        # channel id, so a production .env that omitted the var pointed the
        # upload gate at a channel in another workspace and silently dropped
        # every CSV/JD file (found 2026-09-02). None makes the gate closed and
        # obvious instead of open to the wrong place.
        slack_jobs_channel_id=os.environ.get("BANKS_JOBS_CHANNEL_ID"),
        slack_app_token=os.environ.get("BANKS_SLACK_APP_TOKEN"),
        approver_user_id=os.environ.get("BANKS_APPROVER_USER_ID"),
        josh_email=os.environ.get("BANKS_JOSH_EMAIL"),
        gcp_sa_key=os.environ.get("BANKS_GCP_SA_KEY"),
        calendar_id=os.environ.get("BANKS_CALENDAR_ID"),
        timezone=os.environ.get("BANKS_TIMEZONE", "America/New_York"),
        clay_api_key=os.environ.get("BANKS_CLAY_API_KEY"),
        clay_webhook_url=os.environ.get("BANKS_CLAY_WEBHOOK_URL"),
        enrichment_sheet_id=os.environ.get("BANKS_ENRICHMENT_SHEET_ID"),
        enrichment_sheet_range=os.environ.get("BANKS_ENRICHMENT_SHEET_RANGE", "Sheet1"),
        anthropic_api_key=os.environ.get("BANKS_ANTHROPIC_API_KEY"),
        db_path=os.environ.get("BANKS_DB_PATH", "banks.db"),
        outbox_dir=os.environ.get("BANKS_OUTBOX_DIR", "outbox"),
        exclusions_file=os.environ.get("BANKS_EXCLUSIONS_FILE", "exclusions.txt"),
        targets_file=os.environ.get("BANKS_TARGETS_FILE", "targets.txt"),
        smtp_host=os.environ.get("BANKS_SMTP_HOST"),
        smtp_port=int(os.environ.get("BANKS_SMTP_PORT", "587")),
        smtp_user=os.environ.get("BANKS_SMTP_USER"),
        smtp_password=os.environ.get("BANKS_SMTP_PASSWORD"),
        smtp_from=os.environ.get("BANKS_SMTP_FROM"),
        resend_api_key=os.environ.get("BANKS_RESEND_API_KEY"),
        intake_email=os.environ.get("BANKS_INTAKE_EMAIL"),
        intake_email_password=os.environ.get("BANKS_INTAKE_EMAIL_PASSWORD"),
        proactive_consulting_enabled=os.environ.get(
            "BANKS_PROACTIVE_CONSULTING", "").strip().lower() in ("1", "true", "yes"),
        remote_only_roles=os.environ.get(
            "BANKS_REMOTE_ONLY", "true").strip().lower() in ("1", "true", "yes"),
    )
