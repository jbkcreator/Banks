"""Marks all emails from today as unread in Josh's inbox.
Restores emails accidentally marked read by the hung IMAP poll run.

Run: python scripts/restore_unread.py
"""
import imaplib
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

email_addr = os.environ["BANKS_INTAKE_EMAIL"]
password = os.environ["BANKS_INTAKE_EMAIL_PASSWORD"]
today = datetime.now(timezone.utc).strftime("%d-%b-%Y")

print(f"Connecting to imap.gmail.com as {email_addr} ...")
with imaplib.IMAP4_SSL("imap.gmail.com") as conn:
    conn.login(email_addr, password)
    conn.select("INBOX")
    _, data = conn.search(None, f'(SEEN SINCE "{today}")')
    uids = data[0].split()
    print(f"Found {len(uids)} emails from today marked as read.")
    if not uids:
        print("Nothing to restore.")
    else:
        for uid in uids:
            conn.store(uid, "-FLAGS", "\\Seen")
        print(f"Done — {len(uids)} emails marked unread.")
