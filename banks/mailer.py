"""Outbound sender — the credential Relay holds (R-D1). NOT importable value here.

MailPort's outbound half. Cloudflare Email Routing is inbound-only, so send-as
needs a real sender (Resend, pending Josh's sign-off — client named Cloudflare,
not Resend). This module is imported ONLY by the Relay process; the agent
package must never import it (enforced by the hard-wall send-isolation test).

Fake records sends for tests; Resend sends for real. Both return a provider id.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol


class Mailer(Protocol):
    def send(self, from_addr: str, to_addr: str, subject: str, body: str) -> str: ...


class FakeMailer:
    """Records sends; transmits nothing. For tests + dry runs."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, from_addr: str, to_addr: str, subject: str, body: str) -> str:
        pid = f"fake-{len(self.sent) + 1}"
        self.sent.append({"id": pid, "from": from_addr, "to": to_addr,
                          "subject": subject, "body": body})
        return pid


class ResendMailer:
    """Real outbound via Resend. Key is send-only (least privilege)."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("BANKS_RESEND_API_KEY")
        if not self.api_key:
            raise RuntimeError("No BANKS_RESEND_API_KEY for outbound send.")

    def send(self, from_addr: str, to_addr: str, subject: str, body: str) -> str:
        payload = json.dumps({
            "from": from_addr, "to": [to_addr], "subject": subject, "text": body,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=payload, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     # Resend sits behind Cloudflare, which 1010-blocks the
                     # default Python-urllib UA. Identify ourselves.
                     "User-Agent": "Banks/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read()).get("id", "")


class SmtpMailer:
    """Real outbound via SMTP (STARTTLS). Banks' OWN mailbox — never FA's.

    Uses stdlib smtplib, so no new dependency and no FA import. The credential
    is a Banks-namespaced SMTP account (BANKS_SMTP_*), physically separate from
    Forced Action's Mandrill SMTP — the hard wall holds. Returns the RFC Message-ID
    as the provider id (SMTP has no server-side id like an API), which is stable
    enough for the sent_receipts idempotency claim.
    """

    def __init__(self, host: str | None = None, port: int | None = None,
                 user: str | None = None, password: str | None = None,
                 use_tls: bool = True) -> None:
        self.host = host or os.environ.get("BANKS_SMTP_HOST")
        self.port = port or int(os.environ.get("BANKS_SMTP_PORT", "587"))
        self.user = user or os.environ.get("BANKS_SMTP_USER")
        self.password = password or os.environ.get("BANKS_SMTP_PASSWORD")
        self.use_tls = use_tls
        if not (self.host and self.user and self.password):
            raise RuntimeError(
                "SMTP not configured — need BANKS_SMTP_HOST, BANKS_SMTP_USER, "
                "BANKS_SMTP_PASSWORD for outbound send."
            )

    def send(self, from_addr: str, to_addr: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg_id = make_msgid()
        msg["Message-ID"] = msg_id
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=20) as server:
            if self.use_tls:
                server.starttls()
            server.login(self.user, self.password)
            server.send_message(msg, from_addr=from_addr, to_addrs=[to_addr])
        return msg_id


def load_mailer(config=None):
    """Pick the outbound mailer from config: SMTP if configured, else Resend,
    else refuse. Relay calls this; the agent package must never import it."""
    from .config import load_config
    cfg = config or load_config()
    if cfg.smtp_host and cfg.smtp_user and cfg.smtp_password:
        return SmtpMailer(cfg.smtp_host, cfg.smtp_port, cfg.smtp_user, cfg.smtp_password)
    if os.environ.get("BANKS_RESEND_API_KEY"):
        return ResendMailer()
    raise RuntimeError("No outbound mailer configured (set BANKS_SMTP_* or BANKS_RESEND_API_KEY).")
