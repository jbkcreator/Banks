"""Receipt filing pipeline — domain module (architecture candidate 4).

Extraction, routing, and filing logic lives here; fileport.py is a clean
port (protocol + adapters only). The LLM prompt literal belongs in domain
code, not inside a Drive adapter.
"""

from __future__ import annotations

import email
import email.policy
from dataclasses import dataclass

from .fileport import FilePort


@dataclass
class FiledReceipt:
    filename: str
    drive_id: str
    web_url: str
    vendor: str | None
    amount_cents: int | None
    property_label: str | None


def extract_receipt_from_eml(raw_email: str, llm) -> dict:
    """Extract receipt metadata from .eml string via LLM."""
    system = (
        "Extract receipt/invoice details from this email. "
        "Return ONLY valid JSON with keys: vendor (str), amount_cents (int|null), "
        "date (str ISO-date|null), property_address (str|null), description (str|null)."
    )
    try:
        msg = email.message_from_string(raw_email, policy=email.policy.default)
        subject = msg.get("Subject", "")
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_content()
                    break
        else:
            body = msg.get_content()
        text = f"Subject: {subject}\n\n{body}"[:3000]
    except Exception:
        text = raw_email[:3000]
    return llm.extract_json(system, text)


def resolve_receipt_folder(
    property_address: str | None,
    folder_map: dict[str, str] | None,
    personal_folder_id: str | None,
    default_folder_id: str | None,
) -> str | None:
    """Q20: route per-property; personal folder for non-attributable receipts.

    folder_map: {property_address: drive_folder_id}. If the receipt names a
    property we have a folder for, file it there; otherwise if it names no
    property, use the personal folder; else fall back to the default.
    """
    folder_map = folder_map or {}
    if property_address and property_address in folder_map:
        return folder_map[property_address]
    if not property_address and personal_folder_id:
        return personal_folder_id
    return default_folder_id


def file_receipt_from_eml(
    raw_email: str,
    llm,
    file_port: FilePort,
    parent_folder_id: str | None = None,
    folder_map: dict[str, str] | None = None,
    personal_folder_id: str | None = None,
) -> FiledReceipt:
    """Full E2E: parse .eml → extract metadata → route to per-property Drive folder.

    Per Q20: property receipts file to their property folder, non-attributable
    receipts to a personal folder, and the original attachment is preserved.
    """
    meta = extract_receipt_from_eml(raw_email, llm)
    vendor = meta.get("vendor") or "unknown_vendor"
    date_str = meta.get("date") or __import__("datetime").date.today().isoformat()
    prop_raw = meta.get("property_address")
    prop = prop_raw or "general"
    safe_prop = prop.replace(" ", "_").replace("/", "-")[:40]
    safe_vendor = vendor.replace(" ", "_")[:30]
    filename = f"{date_str}_{safe_vendor}_{safe_prop}.pdf"

    attachment_bytes: bytes | None = None
    mime_type = "application/pdf"
    try:
        msg = email.message_from_string(raw_email, policy=email.policy.default)
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "application/pdf" or part.get_filename():
                attachment_bytes = part.get_payload(decode=True)
                if attachment_bytes:
                    mime_type = ct or "application/octet-stream"
                    break
    except Exception:
        pass
    if not attachment_bytes:
        filename = filename.replace(".pdf", ".txt")
        mime_type = "text/plain"
        attachment_bytes = raw_email.encode()

    target_folder = resolve_receipt_folder(
        prop_raw, folder_map, personal_folder_id, parent_folder_id
    )
    uploaded = file_port.upload(
        name=filename,
        data=attachment_bytes,
        mime_type=mime_type,
        parent_folder_id=target_folder,
    )
    return FiledReceipt(
        filename=filename,
        drive_id=uploaded.drive_id,
        web_url=uploaded.web_url,
        vendor=vendor,
        amount_cents=meta.get("amount_cents"),
        property_label=prop,
    )
