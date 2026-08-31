"""Quick smoke test — verifies SMTP creds by opening a STARTTLS session (login only, no email sent)."""
import sys
import smtplib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from banks.config import load_config

cfg = load_config()

assert cfg.smtp_host, "BANKS_SMTP_HOST not set"
assert cfg.smtp_user, "BANKS_SMTP_USER not set"
assert cfg.smtp_password, "BANKS_SMTP_PASSWORD not set"

print(f"Connecting to {cfg.smtp_host}:{cfg.smtp_port} as {cfg.smtp_user} ...")

with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as s:
    s.ehlo()
    s.starttls()
    s.login(cfg.smtp_user, cfg.smtp_password)
    # No email sent — login success is enough to confirm creds are valid.

print("OK — SMTP login successful. Creds are valid.")
