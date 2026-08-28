"""Manual-test wrapper: load .env, then run the manual-intake CLI live.

Usage (from repo root):
    python scripts/manual_intake_test.py --jd-file jd.txt
    python scripts/manual_intake_test.py --url https://... --title "VP Sales" --company Acme
    python scripts/manual_intake_test.py --title "SDR" --company Ketch      # quick "I applied"

Posts real Slack cards to BANKS_CHANNEL_ID (test workspace) when a role clears
Tier A/B. Needs BANKS_SLACK_BOT_TOKEN + BANKS_ANTHROPIC_API_KEY in .env.
"""
import os
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k] = v
# keep a persistent DB so surfaced drafts survive across runs / listener
os.environ.setdefault("BANKS_DB_PATH", str(root / "banks_live.db"))

from banks.manual_intake import _main  # noqa: E402

raise SystemExit(_main())
