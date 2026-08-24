"""One-time OAuth consent to mint a Drive refresh token for the FilePort live adapter.

Run once per Google account (test now, Josh's account at cutover):

    python scripts/mint_drive_token.py

Opens a browser -> click Allow -> the refresh token is saved to
.secrets/gcp-oauth-token.json (git-ignored). The FilePort live adapter then
uploads receipts owned by that account (works on personal, non-Workspace Drive
where a service account cannot).
"""

from __future__ import annotations

import os

from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT = os.path.join(".secrets", "gcp-oauth-client.json")
TOKEN = os.path.join(".secrets", "gcp-oauth-token.json")
# Full drive scope for the test proof; tighten to drive.file for production
# least-privilege once folder-parenting is confirmed.
SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    print(f"Refresh token saved to {TOKEN}")
    print("has_refresh_token:", bool(creds.refresh_token))


if __name__ == "__main__":
    main()
