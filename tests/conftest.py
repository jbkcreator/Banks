import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from banks.store import init_db  # noqa: E402


@pytest.fixture()
def db_path(tmp_path) -> str:
    path = str(tmp_path / "banks_test.db")
    init_db(path)
    return path


@pytest.fixture(autouse=True)
def _reset_halt():
    """Isolate the kill switch between tests.

    halt.py keeps a module-global `_db_path` so the flag can be shared by the
    listener and scheduler processes (without it, "@banks stop all" halts only
    the process that heard it and Relay keeps sending). That global would
    otherwise leak across tests: one test points halt at its tmp DB, the dir is
    torn down, and every later check_halt() hits an unreadable DB and fail-safes
    to halted. Reset to the in-memory fallback around every test.
    """
    from banks.halt import clear_halt, init_halt
    init_halt(None)
    clear_halt()
    yield
    init_halt(None)
    clear_halt()


@pytest.fixture(scope="session", autouse=True)
def _contain_mkdtemp(tmp_path_factory):
    """Keep bare `tempfile.mkdtemp()` inside pytest's own temp root.

    22 fixtures across 18 test files call tempfile.mkdtemp() with no cleanup, so
    every run leaked a directory into /tmp permanently. That filled a 958M tmpfs
    twice on 2026-09-02 and took the suite down with "database or disk is full".

    Repointing tempfile.tempdir fixes all of them in one place: pytest prunes
    its basetemp (keeping the last few runs), so growth is bounded. Fixing the
    call sites to use tmp_path is still the better change — this stops the
    bleeding without touching 18 files.
    """
    import tempfile
    original = tempfile.tempdir
    tempfile.tempdir = str(tmp_path_factory.mktemp("mkdtemp"))
    yield
    tempfile.tempdir = original
