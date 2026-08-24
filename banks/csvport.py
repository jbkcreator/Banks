"""CSVPort — read CSV files into dicts, plus per-source row parsers.

Column names for LoopCV (Q1) and Simplify (Q2) are placeholders until Josh
exports real files. LinkedIn and alumni column shapes are known.
"""
from __future__ import annotations

import csv
from typing import Protocol

from banks.normalise import normalise_company


# ---------------------------------------------------------------------------
# Port

class CSVPort(Protocol):
    def read_csv(self, path: str) -> list[dict]: ...


class FakeCSVPort:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def read_csv(self, path: str) -> list[dict]:
        return self._rows


class LiveCSVPort:
    def read_csv(self, path: str) -> list[dict]:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# LoopCV  (Q1 — column names TBC once Josh exports real file)

def parse_loopcv_row(row: dict) -> dict:
    return {
        "title": row.get("Job Title") or row.get("title", ""),
        "company": row.get("Company") or row.get("company", ""),
        "source_url": row.get("URL") or row.get("url", ""),
        "source": "loopcv",
    }


# ---------------------------------------------------------------------------
# Simplify  (Q2 — column names TBC)

def parse_simplify_row(row: dict) -> dict:
    return {
        "title": row.get("Role") or row.get("title", ""),
        "company": row.get("Company Name") or row.get("company", ""),
        "source_url": row.get("Job URL") or row.get("url", ""),
        "source": "simplify",
    }


# ---------------------------------------------------------------------------
# LinkedIn connections CSV  (standard LinkedIn export — columns are known)

def parse_linkedin_connection_row(row: dict) -> dict:
    first = row.get("First Name", "")
    last = row.get("Last Name", "")
    return {
        "name": f"{first} {last}".strip(),
        "company": normalise_company(row.get("Company", "")),
        "email": row.get("Email Address", ""),
        "linkedin_url": row.get("Profile URL", ""),
        "degree": 1,
        "source": "linkedin_csv",
    }


# ---------------------------------------------------------------------------
# Alumni CSV  (Q9 — format TBC, map common field names)

def parse_alumni_row(row: dict) -> dict:
    return {
        "name": row.get("name") or row.get("Name", ""),
        "company": normalise_company(row.get("company") or row.get("Company", "")),
        "email": row.get("email") or row.get("Email", ""),
        "linkedin_url": row.get("linkedin_url") or row.get("LinkedIn URL", ""),
        "degree": 1,
        "source": "alumni_csv",
    }
