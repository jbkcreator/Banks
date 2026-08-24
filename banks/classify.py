"""#banks message classifier (B-D3) — LLM classify + confirm-on-ambiguity.

Incoming #banks messages (tenant inquiry, maintenance request, bill forward,
job posting, review request, etc.) are classified here. Ambiguous messages
get a confirm draft sent back to Josh before any action fires.

Classification does NOT fire the action — it returns a ClassifyResult that
the router (socket_listener / inbound mail handler) acts on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llmport import LLMPort

# Canonical message kinds Banks can act on.
MSG_KINDS = (
    "tenant_inquiry",       # prospect asking about a room
    "maintenance_request",  # tenant or vendor message about a repair
    "bill_email",           # forwarded bill / invoice
    "job_posting",          # forwarded job opportunity
    "review_request",       # tenant/guest review nudge
    "occasion_reminder",    # birthday / anniversary / deadline
    "general_note",         # informational, no action
    "unknown",              # LLM couldn't classify with confidence
)

_CLASSIFY_SYSTEM = f"""You classify an incoming message sent to a personal-ops assistant.
Return ONLY valid JSON with keys:
  kind (one of: {', '.join(MSG_KINDS)}),
  confidence (float 0.0-1.0),
  summary (str, one sentence),
  action_hint (str|null, what should happen next).
Be conservative: use "unknown" if confidence < 0.7."""

AMBIGUITY_THRESHOLD = 0.7


@dataclass
class ClassifyResult:
    kind: str
    confidence: float
    summary: str
    action_hint: str | None
    needs_confirm: bool    # True when confidence < threshold or kind == "unknown"
    raw_text: str


def classify_message(text: str, llm: "LLMPort") -> ClassifyResult:
    result = llm.extract_json(_CLASSIFY_SYSTEM, text[:2000])
    kind = result.get("kind") or "unknown"
    confidence = float(result.get("confidence") or 0.0)
    summary = result.get("summary") or text[:80]
    action_hint = result.get("action_hint")
    needs_confirm = kind == "unknown" or confidence < AMBIGUITY_THRESHOLD
    return ClassifyResult(
        kind=kind,
        confidence=confidence,
        summary=summary,
        action_hint=action_hint,
        needs_confirm=needs_confirm,
        raw_text=text,
    )


def ambiguity_confirm_draft(result: ClassifyResult) -> "object":
    """Return a Draft for Josh to confirm when Banks is uncertain."""
    from .enforcement import Draft
    return Draft(
        kind="classify_confirm",
        to="you",
        subject=f"Unsure how to handle this message — your call",
        body=(
            f"Banks received a message it can't confidently classify:\n\n"
            f"  Message: {result.raw_text[:300]}\n\n"
            f"  Best guess: {result.kind} (confidence: {result.confidence:.0%})\n"
            f"  Summary: {result.summary}\n\n"
            f"What should Banks do with this? Reply or approve an action below."
        ),
    )
