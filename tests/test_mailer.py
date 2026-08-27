"""Outbound mailer tests — SmtpMailer (mocked SMTP, no network) + load_mailer."""
from __future__ import annotations

import dataclasses
from unittest import mock

import pytest

from banks.config import load_config
from banks.mailer import FakeMailer, SmtpMailer, load_mailer


def test_fake_mailer_records():
    m = FakeMailer()
    pid = m.send("from@x.com", "to@y.com", "Hi", "Body")
    assert pid == "fake-1"
    assert m.sent[0]["to"] == "to@y.com"


def test_smtp_mailer_sends_via_starttls():
    with mock.patch("banks.mailer.smtplib.SMTP") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        m = SmtpMailer(host="smtp.example.com", port=587,
                       user="banks@example.com", password="app-pw")
        pid = m.send("banks@example.com", "hm@acme.com", "Interest in the role", "Hello.")

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=20)
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("banks@example.com", "app-pw")
    # the frozen payload is what gets transmitted
    args, kwargs = server.send_message.call_args
    assert kwargs["from_addr"] == "banks@example.com"
    assert kwargs["to_addrs"] == ["hm@acme.com"]
    sent_msg = args[0]
    assert sent_msg["To"] == "hm@acme.com"
    assert sent_msg["Subject"] == "Interest in the role"
    assert pid and pid.startswith("<")  # RFC Message-ID as provider id


def test_smtp_mailer_requires_config():
    with pytest.raises(RuntimeError, match="SMTP not configured"):
        SmtpMailer(host=None, user=None, password=None)


def test_load_mailer_prefers_smtp_when_configured():
    cfg = dataclasses.replace(
        load_config(), smtp_host="smtp.example.com",
        smtp_user="banks@example.com", smtp_password="pw",
    )
    assert isinstance(load_mailer(cfg), SmtpMailer)


def test_load_mailer_refuses_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("BANKS_RESEND_API_KEY", raising=False)
    cfg = dataclasses.replace(load_config(), smtp_host=None, smtp_user=None,
                              smtp_password=None)
    with pytest.raises(RuntimeError, match="No outbound mailer configured"):
        load_mailer(cfg)
