"""MOD-03 Surround Pack engine.

On Approve of a Tier A/B opportunity, generates all applicable outreach lanes
as separate Slack cards (each separately approvable by Josh). Decision locked
in BUILD_DECISIONS_MOD03-06.md:
- All applicable lanes generated at once on approve
- POV brief: Tier A only
- Empty career-facts → refuse and report (no-embellishment constitution)
- No warm-contact → no warm-intro card
- Contact with no verified email → LinkedIn card instead of email card
- Frozen company (got-reply signal) → empty pack, nothing sent
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chatport import ChatPort
    from .llmport import LLMPort
    from .opportunity import CareerFacts


@dataclass(frozen=True)
class SurroundPack:
    opportunity_id: int
    lanes: list[dict] = field(default_factory=list)


def generate_surround_pack(
    db_path: str,
    opportunity_id: int,
    career_facts: "CareerFacts",
    chat: "ChatPort",
    llm: "LLMPort | None" = None,
) -> SurroundPack:
    """Generate and post all applicable lanes for an approved Tier A/B opportunity."""
    from .lanes import (
        draft_employee_lane,
        draft_hiring_manager_lane,
        draft_linkedin_lane,
        draft_pov_brief,
        draft_recruiter_lane,
        draft_warm_intro_ask,
    )
    from .flow import propose
    from .governance import is_company_frozen
    from .packets import DecisionPacket
    from .refs import SendChannel
    from .store import cursor

    if career_facts.is_empty():
        raise ValueError(
            "career-facts is empty — Banks cannot draft outreach without verified facts. "
            "Complete career-facts.md first."
        )

    with cursor(db_path) as cur:
        opp = cur.execute(
            "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)
        ).fetchone()

    if not opp:
        raise ValueError(f"opportunity {opportunity_id} not found")

    tier = opp["tier"]
    company = opp["company_normalized"] or ""
    industry = opp["industry"]
    contact_id = opp["contact_id"]
    title = opp["title"]

    if is_company_frozen(db_path, company):
        return SurroundPack(opportunity_id=opportunity_id, lanes=[])

    lanes_created: list[dict] = []

    # Gather all contacts at this company
    warm_contacts: list[dict] = []
    if contact_id:
        with cursor(db_path) as cur:
            row = cur.execute(
                "SELECT * FROM contacts WHERE id = ?", (contact_id,)
            ).fetchone()
            if row:
                warm_contacts.append(dict(row))

    with cursor(db_path) as cur:
        others = cur.execute(
            "SELECT * FROM contacts WHERE company = ? AND id != ?",
            (company, contact_id or -1),
        ).fetchall()
        warm_contacts.extend(dict(r) for r in others)

    primary = warm_contacts[0] if warm_contacts else None

    # Lane: Hiring Manager email (verified) or LinkedIn fallback (unverified)
    if primary:
        has_email = bool(primary.get("verified") and primary.get("email"))
        if has_email:
            draft = draft_hiring_manager_lane(title, company, primary, career_facts, llm)
            lane_type = "hiring_manager"
            channel = SendChannel.SENDAS
        else:
            draft = draft_linkedin_lane(title, company, primary, career_facts)
            lane_type = "linkedin"
            channel = SendChannel.INTERNAL
        lane_id = _create_lane(db_path, opportunity_id, lane_type, primary["id"])
        proposed = propose(
            db_path, _packet(title, lane_type), draft, chat, send_channel=channel
        )
        _set_lane_ref(db_path, lane_id, str(proposed.ref))
        lanes_created.append(
            {"lane_id": lane_id, "type": lane_type, "draft_ref": str(proposed.ref)}
        )

    # Lane: Warm intro (first 1st-degree contact that isn't primary)
    intro_candidates = [
        c for c in warm_contacts
        if c.get("degree") == 1 and c.get("id") != (primary or {}).get("id")
    ]
    # Also include primary if 1st-degree and no dedicated intro yet
    if primary and primary.get("degree") == 1 and not intro_candidates:
        intro_candidates = [primary]

    if intro_candidates:
        ic = intro_candidates[0]
        draft = draft_warm_intro_ask(title, company, ic, career_facts)
        lane_id = _create_lane(db_path, opportunity_id, "warm_intro", ic["id"])
        _create_warm_intro_record(db_path, opportunity_id, ic["id"])
        proposed = propose(
            db_path, _packet(title, "warm_intro"), draft, chat,
            send_channel=SendChannel.INTERNAL,
        )
        _set_lane_ref(db_path, lane_id, str(proposed.ref))
        lanes_created.append(
            {"lane_id": lane_id, "type": "warm_intro", "draft_ref": str(proposed.ref)}
        )

    # Lane: Recruiter (always — standing "keep me on file" note)
    draft = draft_recruiter_lane(title, company, career_facts)
    lane_id = _create_lane(db_path, opportunity_id, "recruiter", None)
    proposed = propose(
        db_path, _packet(title, "recruiter"), draft, chat,
        send_channel=SendChannel.INTERNAL,
    )
    _set_lane_ref(db_path, lane_id, str(proposed.ref))
    lanes_created.append(
        {"lane_id": lane_id, "type": "recruiter", "draft_ref": str(proposed.ref)}
    )

    # Lane: Employee networking (other known contacts, max 2)
    employee_contacts = [
        c for c in warm_contacts
        if c.get("id") != (primary or {}).get("id")
        and c not in intro_candidates
    ]
    for ec in employee_contacts[:2]:
        draft = draft_employee_lane(title, company, ec, career_facts)
        lane_id = _create_lane(db_path, opportunity_id, "employee", ec["id"])
        proposed = propose(
            db_path, _packet(title, "employee"), draft, chat,
            send_channel=SendChannel.INTERNAL,
        )
        _set_lane_ref(db_path, lane_id, str(proposed.ref))
        lanes_created.append(
            {"lane_id": lane_id, "type": "employee", "draft_ref": str(proposed.ref)}
        )

    # Lane: POV brief (Tier A only)
    if tier == "A":
        jd_summary = f"{title} at {company}" + (f" ({industry})" if industry else "")
        draft = draft_pov_brief(title, company, jd_summary, career_facts, llm)
        lane_id = _create_lane(db_path, opportunity_id, "pov_brief", None)
        proposed = propose(
            db_path, _packet(title, "pov_brief"), draft, chat,
            send_channel=SendChannel.INTERNAL,
        )
        _set_lane_ref(db_path, lane_id, str(proposed.ref))
        lanes_created.append(
            {"lane_id": lane_id, "type": "pov_brief", "draft_ref": str(proposed.ref)}
        )

    return SurroundPack(opportunity_id=opportunity_id, lanes=lanes_created)


def advance_warm_intro(
    db_path: str, opportunity_id: int, contact_id: int, new_state: str
) -> None:
    """Manual state advance via Slack button (ASKED → AGREED → INTRODUCED).
    STALLED is set automatically by stall_aged_warm_intros().
    """
    valid = {"ASKED", "AGREED", "INTRODUCED", "STALLED"}
    if new_state not in valid:
        raise ValueError(f"invalid warm_intro state {new_state!r}")
    from .store import cursor
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE warm_intros SET state = ?, state_changed_at = ? "
            "WHERE opportunity_id = ? AND contact_id = ?",
            (new_state, now, opportunity_id, contact_id),
        )


def stall_aged_warm_intros(db_path: str, stall_after_days: int = 7) -> int:
    """Auto-STALL warm intros with no movement after `stall_after_days`. Returns count."""
    import datetime as dt
    from .store import cursor
    cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stall_after_days)
    ).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE warm_intros SET state = 'STALLED', state_changed_at = ? "
            "WHERE state IN ('ASKED', 'AGREED') AND state_changed_at < ?",
            (now, cutoff),
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _packet(title: str, lane_type: str):
    from .packets import DecisionPacket
    label = lane_type.replace("_", " ")
    return DecisionPacket(
        kind=f"outreach_{lane_type}",
        decision=f"Send {label} outreach — {title}",
        recommendation="Review draft and approve to send",
        default_if_unanswered="skip",
        reversible=True,
    )


def _create_lane(
    db_path: str, opportunity_id: int, lane_type: str, contact_id: int | None
) -> int:
    from .store import cursor
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO outreach_lanes "
            "(opportunity_id, lane_type, contact_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (opportunity_id, lane_type, contact_id, now),
        )
        return cur.lastrowid


def _set_lane_ref(db_path: str, lane_id: int, draft_ref: str) -> None:
    from .store import cursor
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE outreach_lanes SET draft_ref = ? WHERE id = ?",
            (draft_ref, lane_id),
        )


def _create_warm_intro_record(
    db_path: str, opportunity_id: int, contact_id: int
) -> None:
    from .store import cursor
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "INSERT OR IGNORE INTO warm_intros "
            "(opportunity_id, contact_id, state, asked_at, state_changed_at) "
            "VALUES (?, ?, 'ASKED', ?, ?)",
            (opportunity_id, contact_id, now, now),
        )
