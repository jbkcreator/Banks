"""MOD-06: exclusions seed from file at startup (as Container.live wires it)."""
from __future__ import annotations

import os
import tempfile

import pytest

from banks.exclusion import (
    is_company_excluded,
    is_person_excluded,
    load_exclusions_from_file,
)
from banks.store import init_db


@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


def _write(dirpath: str, text: str) -> str:
    path = os.path.join(dirpath, "exclusions.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_startup_seed_loads_company_and_person(db_path):
    d = tempfile.mkdtemp()
    path = _write(d, "# launch list\ncompany: Rent Solutions\nperson: Jane Doe\n")
    counts = load_exclusions_from_file(db_path, path)
    assert counts["companies"] >= 1 and counts["people"] >= 1
    assert is_company_excluded(db_path, "Rent Solutions, LLC")  # variant blocked
    assert is_person_excluded(db_path, name="Jane Doe")


def test_startup_seed_is_idempotent(db_path):
    d = tempfile.mkdtemp()
    path = _write(d, "company: Rent Solutions\n")
    load_exclusions_from_file(db_path, path)
    load_exclusions_from_file(db_path, path)  # re-run (every boot) must not duplicate
    from banks.exclusion import list_exclusions
    rows = [r for r in list_exclusions(db_path) if r["company_normalized"] == "rent solutions"]
    assert len(rows) == 1
