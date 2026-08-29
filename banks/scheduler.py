"""Standing-job cadence (Part 5). Eastern by default — a client confirm, not a blocker.

Skeleton only: defines what runs when. Actual job bodies (dashboard assembly,
nightly reflection, scorecard, quarterly optimizer) are the renderer/engine
modules; this module is the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class StandingJob:
    name: str
    # cron-like: "daily HH:MM", "weekly <weekday> HH:MM", "quarterly", "interval"
    cadence: str
    fire_time: time | None = None
    weekday: int | None = None  # 0=Monday ... 4=Friday
    interval_minutes: int | None = None  # cadence == "interval": fire every N minutes


STANDING_JOBS = [
    StandingJob("morning_dashboard", "daily", fire_time=time(7, 30)),
    # MOD-05 Daily Attack Queue — job-search cockpit, same 7:30 ET slot, own post.
    StandingJob("daily_attack_queue", "daily", fire_time=time(7, 30)),
    StandingJob("nightly_reflection", "daily", fire_time=time(23, 0)),
    StandingJob("weekly_scorecard", "weekly", fire_time=time(17, 0), weekday=4),  # Friday
    StandingJob("quarterly_rate_optimizer", "quarterly"),
    StandingJob("weekly_opportunity_cost_meter", "weekly", fire_time=time(17, 0), weekday=4),
    # MOD-01 forwarded email intake — polls the intake mailbox every 10 min.
    # 'interval' (not 'interval_10min') so the live dispatcher (due_jobs) fires it.
    StandingJob("email_intake_poll", "interval", interval_minutes=10),
    # Relay dispatch (PR #9 review gap): an Approve only flips send_intents to
    # 'approved' — something has to actually call relay_run(). Short interval
    # so an approved draft doesn't sit for hours before it's sent.
    StandingJob("relay_dispatch", "interval", interval_minutes=5),
    # MOD-02 contact enrichment (Clay paid, webhook push + Sheet pull). Submit
    # drains the pending queue into Clay hourly; retrieve polls the Sheet buffer
    # every 15 min for rows Clay has written back. Both no-op without creds.
    StandingJob("enrichment_submit", "interval", interval_minutes=60),
    StandingJob("enrichment_retrieve", "interval", interval_minutes=15),
]


def due_jobs_interval(now: datetime, timezone_name: str = "America/New_York") -> list[StandingJob]:
    """Includes interval jobs (every N minutes) alongside the existing time-keyed jobs."""
    due = due_jobs(now, timezone_name)
    for job in STANDING_JOBS:
        if job.cadence == "interval_10min" and now.minute % 10 == 0:
            due.append(job)
    return due


def due_jobs(now: datetime, timezone_name: str = "America/New_York") -> list[StandingJob]:
    """Which standing jobs are due at `now` (checked minute-by-minute by the caller)."""
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    due = []
    for job in STANDING_JOBS:
        if job.cadence == "quarterly":
            continue  # scheduled externally on a quarterly cron, not minute-checked
        if job.cadence == "interval":
            if job.interval_minutes and local_now.minute % job.interval_minutes == 0:
                due.append(job)
            continue
        if job.fire_time is None:
            continue
        if local_now.hour != job.fire_time.hour or local_now.minute != job.fire_time.minute:
            continue
        if job.cadence == "weekly" and local_now.weekday() != job.weekday:
            continue
        due.append(job)
    return due
