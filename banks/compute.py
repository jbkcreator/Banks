"""Compute discipline — LLM tiers, cost logging, daily cap (Phase I T3-15).

Two tiers (E1-E3):
  cheap   — claude-haiku for triage/classification/extraction (nobody reads the raw output)
  premium — claude-sonnet for anything Josh or a guest reads

Per-call cost is logged to activity_log (kind='llm_call', meta has tier+tokens).
Daily cap: if today's spend exceeds DAILY_CAP_CENTS, calls raise DailyCap and
auto-cutoff engages. Cost is surfaced on the weekly scorecard.

Cost estimates (input+output blended, 2026 pricing):
  haiku:   $0.25/M input + $1.25/M output  → ~$0.00025 per 1k tokens blended
  sonnet:  $3.00/M input + $15.00/M output → ~$0.003  per 1k tokens blended
"""

from __future__ import annotations

import os

# Blended per-token costs in USD (input + output average).
COST_PER_TOKEN = {
    "cheap": 0.00000075,    # haiku ~$0.75/M
    "premium": 0.000009,    # sonnet ~$9/M blended
}

# Default daily cap: $1.00 (100 cents). Override via BANKS_DAILY_LLM_CAP_CENTS.
DEFAULT_DAILY_CAP_CENTS = 100


class DailyCap(RuntimeError):
    """Raised when today's LLM spend would exceed the configured cap."""


def daily_cap_cents() -> int:
    return int(os.environ.get("BANKS_DAILY_LLM_CAP_CENTS", DEFAULT_DAILY_CAP_CENTS))


def cost_cents(tier: str, token_count: int) -> int:
    """Estimated cost in cents for a call."""
    rate = COST_PER_TOKEN.get(tier, COST_PER_TOKEN["cheap"])
    return round(rate * token_count * 100)


def log_llm_call(db_path: str, tier: str, token_count: int,
                 purpose: str = "") -> int:
    """Log an LLM call to activity_log. Returns cost_cents."""
    from .activity_log import log_event
    cents = cost_cents(tier, token_count)
    log_event(db_path, "llm_call", meta={
        "tier": tier, "tokens": token_count,
        "cost_cents": cents, "purpose": purpose,
    })
    return cents


def daily_spend_cents(db_path: str, date: str | None = None) -> int:
    """Sum of LLM costs logged today."""
    import json
    from .store import cursor
    from datetime import datetime, timezone
    date = date or datetime.now(timezone.utc).date().isoformat()
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT meta FROM activity_log WHERE kind = 'llm_call' AND ts >= ?",
            (date,),
        ).fetchall()
    total = 0
    for row in rows:
        try:
            total += json.loads(row["meta"] or "{}").get("cost_cents", 0)
        except Exception:
            pass
    return total


def check_daily_cap(db_path: str, tier: str, token_count: int) -> None:
    """Raise DailyCap if adding this call would exceed today's cap."""
    projected = daily_spend_cents(db_path) + cost_cents(tier, token_count)
    cap = daily_cap_cents()
    if projected > cap:
        raise DailyCap(
            f"Daily LLM cap of ${cap/100:.2f} would be exceeded "
            f"(current: ${daily_spend_cents(db_path)/100:.2f}, "
            f"projected: ${projected/100:.2f}). Auto-cutoff engaged."
        )


def weekly_compute_cost_cents(db_path: str, week_ending: str) -> int:
    """Total LLM cost for the scorecard week."""
    import json
    from .store import cursor
    from datetime import date, timedelta
    week_start = (date.fromisoformat(week_ending) - timedelta(days=6)).isoformat()
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT meta FROM activity_log WHERE kind = 'llm_call' "
            "AND ts >= ? AND ts <= ?",
            (week_start, week_ending + "T23:59:59"),
        ).fetchall()
    total = 0
    for row in rows:
        try:
            total += json.loads(row["meta"] or "{}").get("cost_cents", 0)
        except Exception:
            pass
    return total
