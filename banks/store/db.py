"""Banks-local SQLite store. Hard-walled — no FA connection strings, ever.

SQLite is deliberate: one user, tiny data, and — because there is no connection
string or server — the wall between Banks and Forced Action is *physical* rather
than a matter of config discipline. There is no wrong host to point at.

Two access shapes (architecture candidate 5):

* `cursor()`   — one self-contained read or write. Commits on exit.
* `transaction()` — several writes that must land together or not at all.

`transaction()` exists because surfacing a draft writes a decision packet AND a
frozen send intent. Under `cursor()` those were two separate transactions, so a
crash between them left a packet with no intent: a draft that could be approved
but could never send — the client's stated worst case, and invisible to tests,
because tests do not crash mid-function.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

#: Wait rather than fail instantly when another writer holds the lock. Banks now
#: has concurrent writers: the Socket listener (button clicks), the scheduler
#: (standing jobs) and Relay (sends).
_BUSY_TIMEOUT_MS = 5000


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers proceed during a write — the morning brief should never
    # block on Relay. Persistent once set; harmless to re-assert. In-memory DBs
    # silently stay on "memory", which is correct for them.
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:  # pragma: no cover - read-only/locked media
        pass
    return conn


def init_db(db_path: str) -> None:
    """Create all tables if they don't exist. Idempotent."""
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def cursor(db_path: str):
    """One read or one self-contained write. Rolls back if the body raises."""
    conn = connect(db_path)
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction(db_path: str):
    """Several writes as one atomic unit — all commit, or none do.

    Pass the yielded cursor to store functions that accept `cur=` so they join
    this transaction instead of opening their own.
    """
    conn = connect(db_path)
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
