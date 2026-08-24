"""CalendarPort — read-only calendar access (Q23), Fake + live Google.

Feeds schedule.find_conflicts. Read-only *by construction*: the port exposes
only events() — there is no create/update/delete method anywhere, so write
access is structurally unavailable, not merely unused (Q23's explicit ask).
The live adapter authenticates with a service account on the calendar.readonly
scope. Personal/family blocks are returned as ordinary events so they get equal
weight in conflict detection (Q23).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .config import BanksConfig, load_config
from .schedule import CalendarEvent


class CalendarPort(Protocol):
    def events(self, time_min: datetime, time_max: datetime) -> list[CalendarEvent]: ...


class FakeCalendarPort:
    """In-memory calendar for tests — returns events overlapping the window."""

    def __init__(self, events: list[CalendarEvent]) -> None:
        self._events = events

    def events(self, time_min: datetime, time_max: datetime) -> list[CalendarEvent]:
        return [e for e in self._events if e.end > time_min and e.start < time_max]


class GoogleCalendarPort:
    """Live read-only Google Calendar via a service account. No write methods."""

    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

    def __init__(self, config: BanksConfig | None = None) -> None:
        self.config = config or load_config()

    def _service(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            self.config.gcp_sa_key, scopes=self.SCOPES)
        return build("calendar", "v3", credentials=creds)

    @staticmethod
    def _parse(dt: dict) -> datetime:
        # timed events carry dateTime; all-day carry date.
        if "dateTime" in dt:
            return datetime.fromisoformat(dt["dateTime"])
        return datetime.fromisoformat(dt["date"]).replace(tzinfo=timezone.utc)

    def events(self, time_min: datetime, time_max: datetime) -> list[CalendarEvent]:
        resp = self._service().events().list(
            calendarId=self.config.calendar_id,
            timeMin=time_min.astimezone(timezone.utc).isoformat(),
            timeMax=time_max.astimezone(timezone.utc).isoformat(),
            singleEvents=True, orderBy="startTime",
        ).execute()
        out = []
        for e in resp.get("items", []):
            start, end = e.get("start"), e.get("end")
            if not start or not end:
                continue
            out.append(CalendarEvent(
                title=e.get("summary", "(no title)"),
                start=self._parse(start),
                end=self._parse(end),
                owner=e.get("organizer", {}).get("email", "you"),
            ))
        return out
