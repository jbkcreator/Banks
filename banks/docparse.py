"""Document text extraction for PDF, docx, and plain-text JDs.

All extraction is local — no network calls. Used by socket_listener when Josh
drops a JD file tagged @banks; the extracted text is fed into manual_intake's
LLM extractor.

Supported: .pdf (via pypdf), .docx (via python-docx), .txt (raw decode).
Unsupported: anything else → ValueError.

TooLittleText is raised when the extracted text is below min_chars (default 200),
which usually means a scanned/image-only PDF that yielded no selectable text.
"""
from __future__ import annotations

MIN_CHARS_DEFAULT = 200


class TooLittleText(Exception):
    """Raised when extracted text is below the minimum character threshold."""


def extract_text(data: bytes, filename: str, min_chars: int = MIN_CHARS_DEFAULT) -> str:
    """Extract plain text from `data` (raw file bytes).

    `filename` is used only to detect the format by extension.
    Raises ValueError for unsupported types, TooLittleText if result is too short.
    """
    name_lower = (filename or "").lower()
    if name_lower.endswith(".pdf"):
        text = _from_pdf(data)
    elif name_lower.endswith(".docx"):
        text = _from_docx(data)
    elif name_lower.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    else:
        ext = name_lower.rsplit(".", 1)[-1] if "." in name_lower else name_lower
        raise ValueError(f"unsupported file type: {ext!r}")

    text = text.strip()
    if len(text) < min_chars:
        raise TooLittleText(
            f"Extracted only {len(text)} chars from {filename!r} "
            f"(need ≥{min_chars}). Is this a scanned image PDF?"
        )
    return text


def _from_pdf(data: bytes) -> str:
    try:
        import pypdf
    except ImportError as exc:
        raise ImportError("pypdf is required for PDF extraction: pip install pypdf") from exc
    import io
    reader = pypdf.PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            parts.append(t)
    return "\n".join(parts)


def _from_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as exc:
        raise ImportError(
            "python-docx is required for docx extraction: pip install python-docx"
        ) from exc
    import io
    doc = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
