"""One-off TEST seeder — inserts two overlapping events to demo conflict flagging.

Deliberately OUTSIDE the banks/ package: Banks itself is read-only on the
calendar (Q23). This throwaway uses a write scope only to create test data, the
same way a human would in the UI. Requires the SA shared as "Make changes."

    python scripts/seed_calendar.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_KEY = os.environ["BANKS_GCP_SA_KEY"]
CAL = os.environ["BANKS_CALENDAR_ID"]
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _event(svc, summary, start, end):
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    e = svc.events().insert(calendarId=CAL, body=body).execute()
    print("inserted:", summary, e["id"])


def main() -> None:
    creds = service_account.Credentials.from_service_account_file(SA_KEY, scopes=SCOPES)
    svc = build("calendar", "v3", credentials=creds)
    base = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    base = base.replace(hour=14, minute=0)  # tomorrow 14:00 UTC
    _event(svc, "Investor call", base, base + timedelta(hours=1))
    _event(svc, "Family time (personal block)",
           base + timedelta(minutes=30), base + timedelta(hours=2))


if __name__ == "__main__":
    main()
