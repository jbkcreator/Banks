"""Self-healing + temporal memory (Part 5 mechanics, inherited "per v2").

retry-3-then-dead-letter · labeled degradation · temporal-memory freshness
(rent comps 30d, vendor quotes 90d, bills always current; expired = "unknown
because stale").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .store import cursor

MAX_ATTEMPTS = 3

# Freshness windows in days. `None` = always current (never considered stale).
FRESHNESS_DAYS = {
    "rent_comp": 30,
    "vendor_quote": 90,
    "bill": None,
}


class DeadLettered(RuntimeError):
    """Raised when a job has exhausted its retries."""


def record_attempt(db_path: str, job_name: str, attempt: int, status: str,
                    degradation_label: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO job_runs (job_name, started_at, finished_at, attempt, status, degradation_label)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_name, now, now, attempt, status, degradation_label),
        )
        return cur.lastrowid


def run_with_retry(db_path: str, job_name: str, fn):
    """Run `fn()`; on failure retry up to MAX_ATTEMPTS, then dead-letter.

    A degraded-but-successful run (fn returns a tuple (result, degradation_label))
    is recorded with its label rather than treated as a failure.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = fn()
            degradation_label = None
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
                result, degradation_label = result
            record_attempt(
                db_path, job_name, attempt,
                "degraded" if degradation_label else "ok",
                degradation_label,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - deliberately broad; this is the retry boundary
            last_exc = exc
            record_attempt(db_path, job_name, attempt, "failed")

    record_attempt(db_path, job_name, MAX_ATTEMPTS, "dead_letter")
    raise DeadLettered(
        f"'{job_name}' failed {MAX_ATTEMPTS} times; dead-lettered. Last error: {last_exc}"
    ) from last_exc


def is_stale(fact_kind: str, recorded_at: datetime, now: datetime | None = None) -> bool:
    window = FRESHNESS_DAYS.get(fact_kind)
    if window is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - recorded_at > timedelta(days=window)


def record_fact(db_path: str, fact_key: str, fact_kind: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO fact_freshness (fact_key, fact_kind, recorded_at, value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fact_key) DO UPDATE SET
                fact_kind = excluded.fact_kind,
                recorded_at = excluded.recorded_at,
                value = excluded.value
            """,
            (fact_key, fact_kind, now, value),
        )


def read_fact(db_path: str, fact_key: str) -> str | None:
    """Returns the fact's value, or None if it's stale ('unknown because stale')."""
    with cursor(db_path) as cur:
        cur.execute(
            "SELECT fact_kind, recorded_at, value FROM fact_freshness WHERE fact_key = ?",
            (fact_key,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    recorded_at = datetime.fromisoformat(row["recorded_at"])
    if is_stale(row["fact_kind"], recorded_at):
        return None  # unknown because stale
    return row["value"]
