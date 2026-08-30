"""Scheduler runner — the clock that actually fires Banks' standing jobs.

scheduler.py says *which* jobs are due; jobs.run_due_jobs performs them; this is
the long-lived loop that calls run_due_jobs once a minute so the morning queue,
cadence follow-ups, relay dispatch, and the email/enrichment polls fire on their
own. Pairs with socket_listener.py (buttons): two long-running processes, each
its own systemd service.

Run:  python -m banks.run

Design:
- Aligns each tick to the top of the minute so a job at 07:30 fires at 07:30.
- Goes through Container.live() so exclusions.txt + targets.txt are seeded and
  the live Slack ChatPort is wired. Needs the BANKS_* env (systemd EnvironmentFile).
- A halted Banks (halt.py kill switch) skips the tick's work but keeps looping,
  so lifting the halt resumes without a restart. Any per-tick error is logged and
  swallowed — one bad tick must never kill the clock.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


def _log(msg: str) -> None:
    print(f"[banks.run] {datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def _run_one_tick(db_path: str, chat, tz: str, now: datetime | None = None) -> list[str]:
    """Fire the jobs due at `now`, swallowing halt + per-tick errors so the clock
    never dies. Returns the job names run (empty on halt/error). Testable seam."""
    from .halt import BanksHalted
    from .jobs import run_due_jobs
    try:
        ran = run_due_jobs(now or datetime.now(timezone.utc), db_path, chat, tz)
        if ran:
            _log(f"fired: {', '.join(ran)}")
        return ran
    except BanksHalted:
        _log("halted - skipping work this tick (restart-free resume on unhalt)")
        return []
    except Exception as exc:  # one bad tick must not kill the clock
        _log(f"tick error (continuing): {type(exc).__name__}: {exc}")
        return []


def run_forever(tick_seconds: int = 60) -> None:
    from .config import load_config
    from .container import Container

    c = Container.live()
    cfg_tz = load_config().timezone
    _log(f"scheduler up - db={c.db_path} tz={cfg_tz}; ticking every {tick_seconds}s")

    while True:
        # sleep to the top of the next minute so fire_time (HH:MM) matches exactly
        time.sleep(max(0.0, tick_seconds - (time.time() % tick_seconds)))
        _run_one_tick(c.db_path, c.chat, cfg_tz)


if __name__ == "__main__":
    run_forever()
