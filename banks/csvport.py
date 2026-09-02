"""CSVPort — read CSV files into dicts, plus per-source row parsers.

Column names confirmed from Josh's real export files (2026-08-25).
LinkedIn connections CSV has a 3-line preamble — use skip_until_header="First Name".
LoopCV columns remain placeholders until Josh exports (CLIENT_QUERIES.md Q5 dormant).
"""
from __future__ import annotations

import csv
import io
import re
from typing import Protocol

from banks.normalise import normalise_company


# ---------------------------------------------------------------------------
# Port

class CSVPort(Protocol):
    def read_csv(self, path: str, skip_until_header: str | None = None) -> list[dict]: ...


class FakeCSVPort:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def read_csv(self, path: str, skip_until_header: str | None = None) -> list[dict]:
        return self._rows


class LiveCSVPort:
    def read_csv(self, path: str, skip_until_header: str | None = None) -> list[dict]:
        with open(path, newline="", encoding="utf-8-sig") as f:
            lines = f.readlines()
        if skip_until_header:
            for i, line in enumerate(lines):
                if line.strip().startswith(skip_until_header):
                    lines = lines[i:]
                    break
        return list(csv.DictReader(io.StringIO("".join(lines))))


# ---------------------------------------------------------------------------
# LoopCV  — SPEC'D scope (MOD-01 "LoopCV / Simplify Intake"), intentionally
# DORMANT until Josh sets up LoopCV and exports a real file (column names TBC).
# Not speculative — pending a client input.

def parse_loopcv_row(row: dict) -> dict:
    return {
        "title": row.get("Job Title") or row.get("title", ""),
        "company": row.get("Company") or row.get("company", ""),
        "source_url": row.get("URL") or row.get("url", ""),
        "source": "loopcv",
    }


# ---------------------------------------------------------------------------
# Simplify  (confirmed columns from Simplify_Tracked_Jobs_2026-08-24.csv)

def parse_simplify_row(row: dict) -> dict:
    return {
        "title": row.get("Job Title", ""),
        "company": row.get("Company Name", ""),
        "location": row.get("Location", ""),
        "source_url": row.get("Job URL", ""),
        "applied_date": row.get("Applied Date", ""),
        "status": row.get("Status", ""),
        "job_type": row.get("job_type", ""),
        "source": "simplify",
    }


# ---------------------------------------------------------------------------
# LinkedIn connections CSV  (confirmed columns from Connections.csv)
# NOTE: pass skip_until_header="First Name" to LiveCSVPort.read_csv() —
# the export has a 3-line Notes preamble before the real header row.

def parse_linkedin_connection_row(row: dict) -> dict:
    first = row.get("First Name", "")
    last = row.get("Last Name", "")
    return {
        "name": f"{first} {last}".strip(),
        "company": normalise_company(row.get("Company", "")),
        "email": row.get("Email Address", ""),
        "linkedin_url": row.get("URL", ""),
        "position": row.get("Position", ""),
        "connected_on": row.get("Connected On", ""),
        "degree": 1,
        "source": "linkedin_csv",
    }


# ---------------------------------------------------------------------------
# Recruiter registry  (confirmed columns from Banks_Recruiter_Registry.csv)

# Recruiter registry Notes often carry the address inline
# ("Active relationship - intro call Wed 8/26. Email tabitha.francis@lmre.tech").
# It was only ever stored in `notes`, so the one recruiter Josh has a live
# relationship with — with a hand-confirmed work address — was invisible to the
# email lane (found 2026-09-02). Pull it into the email column too.
_NOTES_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _email_from_notes(notes: str) -> str:
    m = _NOTES_EMAIL_RE.search(notes or "")
    return m.group(0).rstrip(".,;") if m else ""


def parse_recruiter_row(row: dict) -> dict:
    first = row.get("First Name", "")
    last = row.get("Last Name", "")
    return {
        "name": f"{first} {last}".strip(),
        "company": normalise_company(row.get("Company", "")),
        "title": row.get("Title", ""),
        "vertical_fit": row.get("Vertical Fit", ""),
        "linkedin_url": row.get("LinkedIn URL", ""),
        "notes": row.get("Notes", ""),
        # Josh sourced these addresses himself, so they are as trustworthy as a
        # provider's — verified=1 makes that provenance explicit on the card.
        "email": row.get("Email", "") or _email_from_notes(row.get("Notes", "")),
        "verified": 1 if (row.get("Email") or _email_from_notes(row.get("Notes", ""))) else 0,
        "degree": 1,
        "source": "recruiter_registry",
    }


# ---------------------------------------------------------------------------
# Alumni CSV  (confirmed columns from Banks_Alumni_FormerColleagues.csv)

def parse_alumni_row(row: dict) -> dict:
    first = row.get("First Name", "")
    last = row.get("Last Name", "")
    return {
        "name": f"{first} {last}".strip(),
        "company": normalise_company(row.get("Company", "")),
        "position": row.get("Position", ""),
        "linkedin_url": row.get("LinkedIn URL", ""),
        "connected_on": row.get("Connected On", ""),
        "degree": 1,
        "source": "alumni_csv",
    }
