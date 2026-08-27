"""MOD-03 Surround Pack engine.

On Approve of a Tier A opportunity, generates all applicable outreach lanes as
separate Slack cards (each separately approvable by Josh). Tier B gets recruiter
lane only — enough to stay visible without blasting a half-qualified opp.

Decisions locked in BUILD_DECISIONS_MOD03-06.md:
- Tier A → full surround pack (HM/LinkedIn + warm intro + recruiter + employee + POV brief)
- Tier B → recruiter lane only
- Empty career-facts → refuse and report (no-embellishment constitution)
- No warm-contact → no warm-intro card
- Contact with no verified email → LinkedIn card instead of email card
- Frozen company (got-reply signal) → empty pack, nothing sent
- pursuit_mode fractional/consulting → consulting lane added
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
    blocked: list[str] = field(default_factory=list)  # contacts skipped for exclusion (MOD-06)


def generate_surround_pack(
    db_path: str,
    opportunity_id: int,
    career_facts: "CareerFacts",
    chat: "ChatPort",
    llm: "LLMPort | None" = None,
) -> SurroundPack:
    """Generate and post all applicable lanes for an approved Tier A/B opportunity.

    Lane rows are created atomically before any Slack posts — a Slack failure
    cannot roll back a persisted decision, but lane rows always exist before refs
    are set so there are no orphaned rows with missing draft_refs.
    """
    from .lanes import (
        draft_consulting_lane,
        draft_employee_lane,
        draft_hiring_manager_lane,
        draft_linkedin_lane,
        draft_pov_brief,
        draft_recruiter_lane,
        draft_warm_intro_ask,
    )
    from .exclusion import (
        is_company_excluded,
        is_conduit_excluded,
        is_contact_excluded,
        is_indirectly_excluded,
    )
    from .flow import propose
    from .governance import is_company_frozen
    from .refs import SendChannel
    from .store import cursor, transaction

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
    pursuit_mode = opp["pursuit_mode"] or ""

    if is_company_frozen(db_path, company):
        return SurroundPack(opportunity_id=opportunity_id, lanes=[])

    # MOD-06 draft-time gate: an excluded company (direct or corporate-variant)
    # produces no pack at all — defensive backstop to the intake gate.
    if is_company_excluded(db_path, company) or is_indirectly_excluded(db_path, company):
        return SurroundPack(opportunity_id=opportunity_id, lanes=[], blocked=[company])

    blocked_contacts: list[str] = []

    def _allowed(contact: dict) -> bool:
        """Person-excluded, or a conduit at an excluded firm → drop the lane."""
        if is_contact_excluded(db_path, contact) or is_conduit_excluded(db_path, contact):
            blocked_contacts.append(contact.get("name") or f"contact {contact.get('id')}")
            return False
        return True

    # Build the spec list: (lane_type, contact_id, draft, channel)
    # Only lanes that have a real target are included.
    lane_specs: list[tuple[str, int | None, object, object]] = []

    # Tier B: recruiter lane only
    if tier != "A":
        draft = draft_recruiter_lane(title, company, career_facts)
        lane_specs.append(("recruiter", None, draft, SendChannel.INTERNAL))
        # Consulting lane if pursuit_mode matches — even for Tier B
        if pursuit_mode in ("fractional", "consulting"):
            draft = draft_consulting_lane(title, company, career_facts)
            lane_specs.append(("consulting", None, draft, SendChannel.INTERNAL))
        return _build_pack(db_path, opportunity_id, title, lane_specs, chat, propose,
                           blocked=blocked_contacts)

    # Tier A: full surround pack
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

    # MOD-06 Q10: drop person-excluded contacts and conduits at excluded firms
    # before any lane is built — the person/indirect gate lives in selection.
    warm_contacts = [c for c in warm_contacts if _allowed(c)]

    primary = warm_contacts[0] if warm_contacts else None

    # Lane: Hiring Manager (email if verified, else LinkedIn card)
    if primary:
        has_email = bool(primary.get("verified") and primary.get("email"))
        if has_email:
            draft = draft_hiring_manager_lane(title, company, primary, career_facts, llm)
            lane_specs.append(("hiring_manager", primary["id"], draft, SendChannel.SENDAS))
        else:
            draft = draft_linkedin_lane(title, company, primary, career_facts)
            lane_specs.append(("linkedin", primary["id"], draft, SendChannel.INTERNAL))

    # Lane: Warm intro (first 1st-degree contact)
    intro_candidates = [
        c for c in warm_contacts
        if c.get("degree") == 1 and c.get("id") != (primary or {}).get("id")
    ]
    if primary and primary.get("degree") == 1 and not intro_candidates:
        intro_candidates = [primary]

    intro_contact_id: int | None = None
    if intro_candidates:
        ic = intro_candidates[0]
        intro_contact_id = ic["id"]
        draft = draft_warm_intro_ask(title, company, ic, career_facts)
        lane_specs.append(("warm_intro", ic["id"], draft, SendChannel.INTERNAL))

    # Lane: Recruiter (always)
    draft = draft_recruiter_lane(title, company, career_facts)
    lane_specs.append(("recruiter", None, draft, SendChannel.INTERNAL))

    # Lane: Employee networking (other contacts, max 2)
    intro_ids = {ic["id"] for ic in intro_candidates}
    employee_contacts = [
        c for c in warm_contacts
        if c.get("id") != (primary or {}).get("id")
        and c.get("id") not in intro_ids
    ]
    for ec in employee_contacts[:2]:
        draft = draft_employee_lane(title, company, ec, career_facts)
        lane_specs.append(("employee", ec["id"], draft, SendChannel.INTERNAL))

    # Lane: Consulting (if pursuit_mode matches)
    if pursuit_mode in ("fractional", "consulting"):
        draft = draft_consulting_lane(title, company, career_facts)
        lane_specs.append(("consulting", None, draft, SendChannel.INTERNAL))

    # Lane: POV brief (Tier A only)
    jd_summary = f"{title} at {company}" + (f" ({industry})" if industry else "")
    draft = draft_pov_brief(title, company, jd_summary, career_facts, llm)
    lane_specs.append(("pov_brief", None, draft, SendChannel.INTERNAL))

    pack = _build_pack(db_path, opportunity_id, title, lane_specs, chat, propose,
                       blocked=blocked_contacts)

    # Register warm-intro state machine rows after lanes are created
    if intro_contact_id is not None:
        _create_warm_intro_record(db_path, opportunity_id, intro_contact_id)

    return pack


def _build_pack(
    db_path: str,
    opportunity_id: int,
    title: str,
    lane_specs: list,
    chat: "ChatPort",
    propose_fn,
    blocked: list | None = None,
) -> SurroundPack:
    """Phase 1: create all lane rows atomically. Phase 2: propose (Slack).
    Phase 3: set draft_refs atomically.
    """
    from .store import transaction

    now = datetime.now(timezone.utc).isoformat()

    # Phase 1 — all inserts in one transaction
    lane_ids: list[int] = []
    with transaction(db_path) as cur:
        for lane_type, contact_id, _draft, _channel in lane_specs:
            cur.execute(
                "INSERT INTO outreach_lanes "
                "(opportunity_id, lane_type, contact_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (opportunity_id, lane_type, contact_id, now),
            )
            lane_ids.append(cur.lastrowid)

    # Phase 2 — propose each lane (Slack posts; intentionally outside transaction)
    proposed_refs: list[str] = []
    for lane_id, (lane_type, _cid, draft, channel) in zip(lane_ids, lane_specs):
        proposed = propose_fn(
            db_path, _packet(title, lane_type), draft, chat, send_channel=channel
        )
        proposed_refs.append(str(proposed.ref))

    # Phase 3 — set all draft_refs in one transaction
    with transaction(db_path) as cur:
        for lane_id, ref in zip(lane_ids, proposed_refs):
            cur.execute(
                "UPDATE outreach_lanes SET draft_ref = ? WHERE id = ?",
                (ref, lane_id),
            )

    lanes_created = [
        {"lane_id": lid, "type": spec[0], "draft_ref": ref}
        for lid, spec, ref in zip(lane_ids, lane_specs, proposed_refs)
    ]
    return SurroundPack(
        opportunity_id=opportunity_id, lanes=lanes_created, blocked=blocked or []
    )


def advance_warm_intro(
    db_path: str, opportunity_id: int, contact_id: int, new_state: str
) -> None:
    """Manual state advance via Slack button: ASKED → AGREED → INTRODUCED.

    STALLED is auto-only (via stall_aged_warm_intros) — passing it here raises.
    """
    valid_manual = {"ASKED", "AGREED", "INTRODUCED"}
    if new_state not in valid_manual:
        raise ValueError(
            f"invalid warm_intro state {new_state!r} — "
            f"manual transitions are ASKED, AGREED, INTRODUCED only; "
            f"STALLED is set automatically after 7 days of inactivity"
        )
    from .store import cursor
    now = datetime.now(timezone.utc).isoformat()
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE warm_intros SET state = ?, state_changed_at = ? "
            "WHERE opportunity_id = ? AND contact_id = ?",
            (new_state, now, opportunity_id, contact_id),
        )


def stall_aged_warm_intros(db_path: str, stall_after_days: int = 7) -> int:
    """Auto-STALL warm intros with no movement after `stall_after_days`.

    For each newly-stalled intro, creates a secondary_escalation lane row so
    the brief can surface a single recommended follow-up (never auto-sent).
    Returns number of intros stalled.
    """
    import datetime as dt
    from .store import cursor, transaction

    cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stall_after_days)
    ).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    with cursor(db_path) as cur:
        to_stall = cur.execute(
            "SELECT id, opportunity_id, contact_id FROM warm_intros "
            "WHERE state IN ('ASKED', 'AGREED') AND state_changed_at < ?",
            (cutoff,),
        ).fetchall()

    if not to_stall:
        return 0

    ids = [r["id"] for r in to_stall]
    placeholders = ",".join("?" * len(ids))

    with transaction(db_path) as cur:
        cur.execute(
            f"UPDATE warm_intros SET state = 'STALLED', state_changed_at = ? "
            f"WHERE id IN ({placeholders})",
            [now] + ids,
        )
        # One secondary-escalation recommendation per stalled intro
        for row in to_stall:
            cur.execute(
                "INSERT INTO outreach_lanes "
                "(opportunity_id, lane_type, contact_id, status, created_at) "
                "VALUES (?, 'secondary_escalation', ?, 'pending', ?)",
                (row["opportunity_id"], row["contact_id"], now),
            )

    return len(ids)


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
