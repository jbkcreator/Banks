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
import urllib.request
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
