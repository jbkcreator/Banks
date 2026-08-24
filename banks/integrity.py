"""Immutable Core integrity check (Part 5: "hashed; only Josh edits directly").

The constitution's Immutable Core section is checksummed. On load, Banks
recomputes the hash of that section and compares it to the last-approved
value in `constitution.hash`. A mismatch means the hard rules were edited
outside of Josh's explicit approval — Banks halts rather than run under
unverified rules.

To approve a legitimate edit: Josh (or whoever holds the operating copy)
regenerates the hash file after reviewing the change — `write_approved_hash()`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_CORE_HEADER = "## HARD RULES — IMMUTABLE CORE"
_NEXT_HEADER_RE = re.compile(r"^## ", re.MULTILINE)


class ImmutableCoreTampered(RuntimeError):
    """Raised when the constitution's Immutable Core hash doesn't match."""


def extract_immutable_core(constitution_text: str) -> str:
    """Pull just the Immutable Core section out of the constitution file."""
    start = constitution_text.find(_CORE_HEADER)
    if start == -1:
        raise ValueError("Constitution has no '## HARD RULES — IMMUTABLE CORE' section")
    rest = constitution_text[start + len(_CORE_HEADER) :]
    m = _NEXT_HEADER_RE.search(rest)
    core_body = rest[: m.start()] if m else rest
    return (_CORE_HEADER + core_body).strip()


def compute_hash(core_text: str) -> str:
    return hashlib.sha256(core_text.encode("utf-8")).hexdigest()


def write_approved_hash(constitution_path: Path, hash_path: Path) -> str:
    """Josh-only operation: approve the current Immutable Core as authoritative."""
    text = constitution_path.read_text(encoding="utf-8")
    digest = compute_hash(extract_immutable_core(text))
    hash_path.write_text(digest + "\n", encoding="utf-8")
    return digest


def verify(constitution_path: Path, hash_path: Path) -> None:
    """Halt-on-tamper check. Call this on every Banks startup."""
    if not hash_path.exists():
        raise ImmutableCoreTampered(
            f"No approved hash at {hash_path}. Run write_approved_hash() after Josh "
            f"reviews the constitution before Banks may start."
        )
    text = constitution_path.read_text(encoding="utf-8")
    current = compute_hash(extract_immutable_core(text))
    approved = hash_path.read_text(encoding="utf-8").strip()
    if current != approved:
        raise ImmutableCoreTampered(
            "Immutable Core hash mismatch — the hard rules were edited without "
            "Josh's approval. Banks will not run. If this edit is legitimate, "
            "Josh must review it and re-approve via write_approved_hash()."
        )
