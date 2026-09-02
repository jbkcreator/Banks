"""Dependency injection container (A-D7).

One place that wires all ports to their live or fake adapters. Tests swap out
adapters by constructing a Container with Fake* instances. Production calls
Container.live() which reads env vars and raises if any required credential
is absent.

Rule: no port is constructed twice — the container holds the single instance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .calendarport import CalendarPort, FakeCalendarPort, GoogleCalendarPort
from .chatport import ChatPort, FakeChatPort, LiveChatPort
from .fileport import FakeFilePort, FilePort, GoogleDriveFilePort
from .llmport import ClaudeLLMPort, FakeLLMPort, LLMPort
from .mailer import FakeMailer, Mailer, load_mailer


@dataclass
class Container:
    chat: ChatPort
    mailer: Mailer
    llm: LLMPort
    calendar: CalendarPort
    files: FilePort
    db_path: str = "banks.db"

    @classmethod
    def fake(cls, db_path: str = ":memory:") -> "Container":
        """All-Fake container for tests — no env vars, no network."""
        from .store import init_db
        if db_path != ":memory:":
            init_db(db_path)
        return cls(
            chat=FakeChatPort(),
            mailer=FakeMailer(),
            llm=FakeLLMPort(),
            calendar=FakeCalendarPort([]),
            files=FakeFilePort(),
            db_path=db_path,
        )

    @classmethod
    def live(cls, db_path: str | None = None) -> "Container":
        """Live container — reads env vars, raises ValueError on missing required creds."""
        from pathlib import Path
        from .config import load_config
        from .integrity import verify
        from .store import init_db

        # Integrity check first — halt if Immutable Core was edited without approval.
        _pkg = Path(__file__).parent
        verify(_pkg / "constitution.md", _pkg / "constitution.hash")

        cfg = load_config()
        resolved_db = db_path or cfg.db_path
        init_db(resolved_db)
        # Point the kill switch at the shared DB. Both long-running services go
        # through Container.live(), so this is what lets "@banks stop all" in
        # the listener actually stop relay_dispatch in the scheduler.
        from .halt import init_halt
        init_halt(resolved_db)

        # MOD-06: seed the exclusion wall from its source-of-truth file at
        # startup, so the DB the gates check always reflects exclusions.txt.
        # Guarded: a missing file is fine (nothing to seed), never blocks boot.
        if Path(cfg.exclusions_file).exists():
            from .exclusion import load_exclusions_from_file
            load_exclusions_from_file(resolved_db, cfg.exclusions_file)

        # MOD-01 (item 6): seed the target watchlist from targets.txt, same
        # startup pattern. Missing file is fine — no targets, no boost.
        if Path(cfg.targets_file).exists():
            from .targets import load_targets_from_file
            load_targets_from_file(resolved_db, cfg.targets_file)

        # Chat — required for Banks to function.
        if not cfg.slack_ready:
            raise ValueError(
                "Slack credentials missing: set BANKS_SLACK_BOT_TOKEN + BANKS_CHANNEL_ID"
            )
        chat = LiveChatPort(cfg)

        # Mailer — required for relay. One selector (mailer.load_mailer): SMTP if
        # configured, else Resend. Previously hardcoded Resend, so SMTP was dead
        # in prod; now both are reachable through the single policy.
        mailer = load_mailer(cfg)

        # LLM — optional, falls back to Fake (pipeline still runs, extracts are stubbed).
        llm: LLMPort
        if os.environ.get("BANKS_ANTHROPIC_API_KEY"):
            llm = ClaudeLLMPort()
        else:
            llm = FakeLLMPort()

        # Calendar — optional, falls back to Fake.
        calendar: CalendarPort
        if cfg.gcp_sa_key and cfg.calendar_id:
            calendar = GoogleCalendarPort(cfg)
        else:
            calendar = FakeCalendarPort([])

        # Files — optional, falls back to Fake.
        files: FilePort
        token_path = os.environ.get("BANKS_DRIVE_OAUTH_TOKEN_PATH")
        if token_path:
            files = GoogleDriveFilePort(token_path=token_path)
        else:
            files = FakeFilePort()

        return cls(
            chat=chat,
            mailer=mailer,
            llm=llm,
            calendar=calendar,
            files=files,
            db_path=resolved_db,
        )
