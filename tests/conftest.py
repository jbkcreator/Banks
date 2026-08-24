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
