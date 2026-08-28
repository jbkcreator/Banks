"""Test runner: load .env + point at banks_live.db, then run the Socket listener.

Not part of the package — a convenience for live button testing only.
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
os.environ["BANKS_DB_PATH"] = str(root / "banks_live.db")

from banks.socket_listener import run  # noqa: E402

run()
