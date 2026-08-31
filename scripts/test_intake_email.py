"""E2E smoke test for email intake. Polls IMAP once and prints results.

Before running:
  1. Send/forward a job confirmation email to jbkantor@gmail.com (leave it unread)
  2. Run: python scripts/test_intake_email.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from banks.config import load_config
from banks.emailport import LiveImapEmailPort
from banks.intake import ingest_email_confirmations
from banks.store import init_db

cfg = load_config()

assert cfg.intake_email, "BANKS_INTAKE_EMAIL not set"
assert cfg.intake_email_password, "BANKS_INTAKE_EMAIL_PASSWORD not set"

init_db(cfg.db_path)
print(f"Polling {cfg.intake_email} ...")
port = LiveImapEmailPort(cfg.intake_email, cfg.intake_email_password)

ingested, skipped = ingest_email_confirmations(cfg.db_path, port, chat=None)
print(f"Done — ingested={ingested}, skipped={skipped}")
