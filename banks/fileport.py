"""FilePort — Drive file storage port (protocol + adapters).

Fake: in-memory, records uploads. Live: uses the OAuth refresh token minted
by scripts/mint_drive_token.py (not a service account — SA can't write to
personal Drive). Requires BANKS_DRIVE_OAUTH_TOKEN_PATH and BANKS_DRIVE_FOLDER_ID.

Receipt domain logic (FiledReceipt, extract_receipt_from_eml,
resolve_receipt_folder, file_receipt_from_eml) lives in banks.receipts.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol


@dataclass
class UploadedFile:
    name: str
    drive_id: str
    web_url: str


class FilePort(Protocol):
    def upload(self, name: str, data: bytes, mime_type: str,
               parent_folder_id: str | None = None) -> UploadedFile: ...


class FakeFilePort:
    def __init__(self) -> None:
        self.uploads: list[dict] = []

    def upload(self, name: str, data: bytes, mime_type: str,
               parent_folder_id: str | None = None) -> UploadedFile:
        entry = {"name": name, "size": len(data), "mime_type": mime_type,
                 "parent_folder_id": parent_folder_id}
        self.uploads.append(entry)
        fake_id = f"fake_{len(self.uploads)}"
        return UploadedFile(name=name, drive_id=fake_id,
                            web_url=f"https://drive.google.com/fake/{fake_id}")


class GoogleDriveFilePort:
    """Live Google Drive uploader using OAuth token (user-delegated, not SA)."""

    UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, token_path: str | None = None, folder_id: str | None = None) -> None:
        self._token_path = token_path or os.environ.get("BANKS_DRIVE_OAUTH_TOKEN_PATH")
        self._folder_id = folder_id or os.environ.get("BANKS_DRIVE_FOLDER_ID")
        if not self._token_path:
            raise ValueError("BANKS_DRIVE_OAUTH_TOKEN_PATH not set")

    def _access_token(self) -> str:
        with open(self._token_path) as f:
            tok = json.load(f)
        if "access_token" in tok and not self._is_expired(tok):
            return tok["access_token"]
        # Refresh.
        client_path = os.environ.get("BANKS_DRIVE_OAUTH_CLIENT_PATH")
        if not client_path:
            raise ValueError("BANKS_DRIVE_OAUTH_CLIENT_PATH not set for refresh")
        with open(client_path) as f:
            client = json.load(f).get("installed") or json.load(f)
        payload = urllib.parse.urlencode({
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(self.TOKEN_URL, data=payload,
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "Banks/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            new_tok = json.loads(resp.read())
        tok["access_token"] = new_tok["access_token"]
        with open(self._token_path, "w") as f:
            json.dump(tok, f, indent=2)
        return tok["access_token"]

    @staticmethod
    def _is_expired(tok: dict) -> bool:
        import time
        expiry = tok.get("token_expiry") or tok.get("expiry")
        if not expiry:
            return True
        try:
            exp_ts = float(expiry) if str(expiry).replace(".", "").isdigit() else \
                __import__("datetime").datetime.fromisoformat(str(expiry)).timestamp()
            return time.time() > exp_ts - 60
        except Exception:
            return True

    def upload(self, name: str, data: bytes, mime_type: str,
               parent_folder_id: str | None = None) -> UploadedFile:
        token = self._access_token()
        folder = parent_folder_id or self._folder_id
        meta = {"name": name}
        if folder:
            meta["parents"] = [folder]
        boundary = "banks_boundary_01"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps(meta)
            + f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        req = urllib.request.Request(
            self.UPLOAD_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
                "User-Agent": "Banks/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        return UploadedFile(
            name=name,
            drive_id=result["id"],
            web_url=result.get("webViewLink", f"https://drive.google.com/file/d/{result['id']}"),
        )


# Back-compat re-exports so existing imports from fileport still resolve.
from .receipts import (  # noqa: E402
    FiledReceipt,
    extract_receipt_from_eml,
    file_receipt_from_eml,
    resolve_receipt_folder,
)
