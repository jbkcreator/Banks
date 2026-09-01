"""MOD-01/02 intake orchestration — the one path that turns a CSV into recorded,
scored, tiered opportunities and a populated contact graph.

This is the seam the unit-tested pieces (csvport, dedup, normalise, score,
exclusion) plug into. Nothing here sends or submits.

**Decision 4 (surface policy).** Simplify exports carry no salary and no
industry, so comp and vertical fall to their neutral default — tiering is
half-blind. Such rows are recorded with needs_enrichment=1 and are NOT surfaced
to Slack; if we surfaced them, every role would clear Tier B and flood the
channel. Surfacing waits until enrichment fills comp+vertical, the score is
recomputed, and needs_enrichment flips to 0.

**Decision 6 (Clay is manual on free tier).** `export_enrichment_queue()` writes
the roles still missing comp/contact to a CSV Josh runs through Clay by hand,
then re-imports. No automated Clay call — the free tier blocks webhook/API/Sheets.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone

from . import score as _score
from .chatport import ChatPort
from .csvport import CSVPort, parse_simplify_row
from .dedup import find_duplicate, find_duplicate_contact
from .enforcement import Draft
from .exclusion import is_target_excluded
from .flow import Proposed, propose
from .normalise import (classify_pursuit_mode, classify_role_type,
                        map_simplify_status, normalise_company)
from .opportunity import mark_application_drafted, record_opportunity
from .packets import DecisionPacket
from .store import cursor
from .warmpath import (attach_contact, describe_contact, find_referral_paths,
                       find_warm_contacts)

# MERGE priority (Decision 5): when the same person appears in several import
# files, which source LABEL to keep. `manual` ranks HIGHEST — a hand-added label
# is a deliberate override. NOTE: intentionally distinct from
# warmpath._SOURCE_RANK (outreach *warmth*), where `manual` ranks lowest —
# different purpose, so they are not shared.
_SOURCE_PRIORITY = {"linkedin_csv": 1, "alumni_csv": 2, "recruiter_registry": 3, "manual": 4}


@dataclass(frozen=True)
class IntakeResult:
    ingested: int          # new opportunities recorded
    duplicates: int        # skipped as dupes
    excluded: int          # skipped on exclusion list
    held: int              # recorded but held for enrichment (not surfaced)
    surfaced: int          # Tier A/B posted to Slack
    proposals: list[Proposed]


def _score_row(parsed: dict, comp_k: float | None, vertical: str | None,
               target_priority: int | None = None) -> tuple[int, str, str, bool]:
    """Return (fit_score, tier, pursuit_mode, needs_enrichment) — via score.score_role.

    target_priority (watchlist, item 6) applies a graded company bump; role_type
    (item 7) screens the title; remote_only (2026-08-31) sinks hybrid/onsite roles
    outside the home market.
    """
    from .config import load_config
    title_blob = f"{parsed.get('title', '')} {parsed.get('job_type', '')}"
    pursuit_mode = classify_pursuit_mode(title_blob)
    role_type = classify_role_type(parsed.get("title", ""), parsed.get("description", ""))
    fit, tier, needs_enrichment = _score.score_role(
        comp_k=comp_k, industry=vertical,
        location=parsed.get("location", ""), pursuit_mode=pursuit_mode,
        target_priority=target_priority, role_type=role_type,
        remote_only=load_config().remote_only_roles)
    return fit, tier, pursuit_mode, needs_enrichment


def ingest_simplify(
    db_path: str,
    csvport: CSVPort,
    path: str,
    chat: ChatPort,
    *,
    surface_tiers: tuple[str, ...] = ("A", "B"),
) -> IntakeResult:
    """Run the MOD-01 intake pipeline over a Simplify export.

    Per row: parse -> exclusion -> dedup -> normalise -> classify -> score ->
    tier -> record. Rows missing comp/vertical (all Simplify rows) are held with
    needs_enrichment=1 and NOT surfaced (Decision 4). Only fully-scored Tier A/B
    rows surface to #banks.
    """
    rows = csvport.read_csv(path)
    ingested = duplicates = excluded = held = surfaced = 0
    proposals: list[Proposed] = []

    for raw in rows:
        parsed = parse_simplify_row(raw)
        title = parsed["title"].strip()
        company = parsed["company"].strip()
        if not title or not company:
            continue

        if is_target_excluded(db_path, company=company)[0]:
            excluded += 1
            continue

        simplify_status = map_simplify_status(parsed.get("status", ""))

        source_url = parsed["source_url"].strip() or None
        if find_duplicate(db_path, source_url, title, company) is not None:
            duplicates += 1
            continue

        # Simplify carries neither salary nor industry.
        from .targets import target_priority
        fit, tier, pursuit_mode, needs_enrichment = _score_row(
            parsed, comp_k=None, vertical=None,
            target_priority=target_priority(db_path, company))

        opp_id = record_opportunity(
            db_path, title, parsed["source"], fit,
            tier=tier, pursuit_mode=pursuit_mode,
            company_normalized=normalise_company(company), source_url=source_url,
            needs_enrichment=needs_enrichment,
            status=simplify_status,
        )
        ingested += 1

        # Closed rows are recorded for dedup/history but never surfaced.
        if simplify_status == "closed":
            held += 1
            continue

        if needs_enrichment:
            held += 1
            continue

        if tier in surface_tiers:
            proposals.append(_surface_opportunity(db_path, chat, opp_id, parsed, fit, tier, pursuit_mode))
            mark_application_drafted(db_path, opp_id)
            surfaced += 1

    return IntakeResult(ingested, duplicates, excluded, held, surfaced, proposals)


def _surface_opportunity(db_path, chat, opp_id, parsed, fit, tier, pursuit_mode) -> Proposed:
    title, company = parsed["title"], parsed["company"]
    loc = parsed.get("location", "")

    # MOD-01 ↔ MOD-02 join (P2): warm-intro + referral paths for this company.
    industry = parsed.get("industry")
    paths = find_referral_paths(db_path, company, industry, limit=3)
    direct = [p for p in paths if p.get("path") == "direct"]
    if direct:
        attach_contact(db_path, opp_id, direct[0]["id"])  # attach warmest direct

    if paths:
        warm_block = ("\n\nWarm path — %d option(s):\n%s"
                      % (len(paths), "\n".join(f"  • {describe_contact(c)}" for c in paths)))
        warm_evidence = f" · warm: {describe_contact(paths[0])}"
    else:
        # Cold company, no direct contact and no matching recruiter (Q8): queue it
        # for contact enrichment (find the requisition owner + verified email).
        from .contact_enrichment import enqueue_company
        enqueue_company(db_path, normalise_company(company), role_hint=title,
                        opportunity_id=opp_id)
        warm_block = "\n\nWarm path: no known contacts yet — queued for enrichment."
        warm_evidence = " · warm: none"

    packet = DecisionPacket(
        kind="opportunity",
        decision=f"Pursue Tier {tier} role: {title} at {company}?",
        recommendation=f"Surround this role (Tier {tier}, fit {fit}/100).",
        alternative="Monitor only — leave in queue.",
        evidence=f"{title} · {company} · {loc} · pursuit: {pursuit_mode} · fit {fit}/100{warm_evidence}",
        default_if_unanswered="Leave in queue (no action).",
        reversible=True,
    )
    draft = Draft(
        kind="opportunity",
        to="(queued — you decide)",
        subject=f"Tier {tier} — {title} at {company}",
        body=(
            f"New opportunity scored and tiered.\n\n"
            f"Role: {title}\nCompany: {company}\nLocation: {loc}\n"
            f"Pursuit mode: {pursuit_mode}\nFit score: {fit}/100 (Tier {tier})"
            f"{warm_block}"
        ),
    )
    proposed = propose(db_path, packet, draft, chat)
    with cursor(db_path) as cur:
        cur.execute(
            "UPDATE opportunities SET source_packet_id = ? WHERE id = ?",
            (proposed.packet_id, opp_id),
        )
    return proposed


_EMAIL_CONFIRM_SYSTEM = (
    "The user applied to jobs at these companies (via Simplify): {companies}. "
    "Decide whether the email below is a genuine confirmation that the user's JOB "
    "APPLICATION WAS RECEIVED by ONE of those companies. Marketing, newsletters, "
    "promotions, product/loan offers, rejections, interview invitations, one-time "
    "passcodes, and any unrelated mail are NOT application confirmations. "
    'Return JSON only: {{"confirmed": true or false, '
    '"company": "<exact name copied from the list, or null>"}}.'
)


# Statuses a confirmation email must never touch. 'confirmed' avoids
# double-processing; 'interviewing'/'closed' are terminal for cadence
# (due_cadence_touches suppresses follow-ups on them) so a late/stray confirmation
# must not reopen them. Keep in sync with governance.due_cadence_touches.
_SETTLED_STATUSES = ("confirmed", "interviewing", "closed")


def ingest_email_confirmations(
    db_path: str,
    email_port,
    chat: "ChatPort",
    llm=None,
) -> tuple[int, int]:
    """MOD-01 email confirmation listener — MATCH-AND-CONFIRM only.

    Email is NOT used to discover new applications (a noisy personal inbox floods
    junk). Instead each candidate email is matched against a company Josh actually
    applied to — the opportunities already recorded from the Simplify CSV / manual
    intake — and an LLM confirms it's a genuine "application received" for that
    company. On a confirmed match we mark that opportunity `confirmed`; we NEVER
    create a new opportunity from email, and anything that doesn't match a known
    application is discarded (never logged). Returns (confirmed, skipped).

    The LLM is the gate: with no llm we confirm nothing (fail safe, no junk).
    """
    from .normalise import normalise_company

    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT id, company_normalized, status FROM opportunities "
            "WHERE company_normalized IS NOT NULL AND company_normalized != ''"
        ).fetchall()
    known = [(r["id"], r["company_normalized"], r["status"]) for r in rows]

    messages = email_port.get_confirmations()
    confirmed = skipped = 0

    for msg in messages:
        subject = msg.get("subject", "")
        body = msg.get("body", "")
        frm = msg.get("from", "")
        blob = f"{subject}\n{frm}\n{body}".lower()

        # Narrow: candidate applications whose company name appears in the email.
        # Exclude any already-settled status (confirmed OR terminal): a late/duplicate
        # confirmation must never reopen a closed/interviewing opp back to 'confirmed'
        # (which would re-arm cadence follow-ups). _SETTLED is the guard.
        cands = [k for k in known if k[1] and k[1] in blob]
        cands = [k for k in cands if k[2] not in _SETTLED_STATUSES]
        if not cands:
            skipped += 1
            continue
        if llm is None:
            skipped += 1  # confirmation requires the LLM gate — fail safe
            continue

        names = sorted({k[1] for k in cands})
        try:
            data = llm.extract_json(
                _EMAIL_CONFIRM_SYSTEM.format(companies=", ".join(names)),
                f"Subject: {subject}\nFrom: {frm}\n\n{body[:2000]}",
            )
        except Exception:
            skipped += 1
            continue
        if not data.get("confirmed"):
            skipped += 1
            continue

        target = normalise_company(data.get("company") or "")
        matched = next((k for k in cands if k[1] == target), None) or cands[0]
        opp_id, company = matched[0], matched[1]

        # Conditional update: only transition from a non-settled status, and only
        # act (funnel event + receipt) when THIS statement actually changed a row.
        # Guards two races: (a) a terminal status slipping through, (b) two
        # confirmation emails for the same app in one poll — the second finds the
        # row already 'confirmed' and changes 0 rows, so no duplicate event/receipt.
        with cursor(db_path) as cur:
            cur.execute(
                "UPDATE opportunities SET status='confirmed' "
                "WHERE id=? AND status NOT IN (%s)"
                % ",".join("?" * len(_SETTLED_STATUSES)),
                (opp_id, *_SETTLED_STATUSES),
            )
            changed = cur.rowcount
            det = cur.execute("SELECT title, source_url FROM opportunities WHERE id=?",
                              (opp_id,)).fetchone()
        if not changed:
            skipped += 1  # already settled between snapshot and now — no double count
            continue
        # Reflect the new state in the in-poll snapshot so a later message in the
        # same poll won't re-select this now-confirmed opportunity.
        known = [(i, c, "confirmed" if i == opp_id else s) for (i, c, s) in known]
        from .governance import record_funnel_event
        record_funnel_event(db_path, opp_id, "application_confirmed")
        print(f"[intake] confirmed application: company={company!r} opp={opp_id}")
        _post_intake_receipt(chat, company, role=(det["title"] if det else "") or "",
                             source_url=(det["source_url"] if det else None),
                             message_id=msg.get("message_id"))
        confirmed += 1

    print(f"[intake] done — confirmed={confirmed} skipped={skipped}")
    return confirmed, skipped


def _gmail_link(message_id: str | None) -> str | None:
    """Deep-link to the exact confirmation email in Josh's Gmail. The
    rfc822msgid: search form resolves regardless of folder/label."""
    if not message_id or not message_id.strip():
        return None
    import urllib.parse
    mid = message_id.strip().strip("<>")
    return ("https://mail.google.com/mail/u/0/#search/rfc822msgid:"
            + urllib.parse.quote(mid))


def _posting_link(source_url: str | None, role: str, company: str) -> str:
    """The job posting URL if we have it, else a LinkedIn jobs search built from
    role + company so the receipt always has a live link to the role."""
    if source_url and source_url.strip():
        return source_url.strip()
    import urllib.parse
    q = urllib.parse.quote_plus(f"{role} {company}".strip())
    return f"https://www.linkedin.com/jobs/search/?keywords={q}"


def _post_intake_receipt(chat: "ChatPort", company: str, role: str = "",
                         source_url: str | None = None,
                         message_id: str | None = None) -> None:
    """Clear, actionable Slack receipt when an application is confirmed: which
    company + role, a link back to the posting, a link to the confirmation email,
    and the two useful next moves. A Slack failure must never sink the intake."""
    if chat is None:
        return
    display = company.title() if company and company.islower() else company
    lines = [f":white_check_mark: *Application confirmed* — *{display}* received "
             f"your application."]
    if role:
        lines.append(f"*Role:* {role}")
    refs = [f"<{_posting_link(source_url, role, display)}|View the job posting>"]
    gm = _gmail_link(message_id)
    if gm:
        refs.append(f"<{gm}|View the confirmation email>")
    lines.append("*Links:* " + "  ·  ".join(refs))
    lines.append(
        f"*What next?* Ask `@banks who do I know at {display}` to find a warm "
        f"intro, or `@banks status {display}` to check where it stands.")
    text = "\n".join(lines)
    try:
        chat.post_blocks(text, [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        ])
    except Exception as exc:  # visibility is a bonus, never a failure mode
        print(f"[intake] receipt post failed (continuing): {type(exc).__name__}: {exc}")


def export_enrichment_queue(db_path: str, out_path: str) -> int:
    """Decision 6: write roles still needing comp/vertical to a CSV for Josh to
    run through Clay by hand (free tier blocks automated enrichment). Returns the
    row count — the caller can post a Slack nudge ("N roles need a Clay pass").
    """
    with cursor(db_path) as cur:
        rows = cur.execute(
            "SELECT title, company_normalized, source_url FROM opportunities "
            "WHERE needs_enrichment = 1 ORDER BY id"
        ).fetchall()
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["title", "company", "source_url"])
        for r in rows:
            w.writerow([r["title"], r["company_normalized"], r["source_url"] or ""])
    return len(rows)


# ---------------------------------------------------------------------------
# Contact graph ingestion (MOD-02)

def ingest_contacts(
    db_path: str,
    csvport: CSVPort,
    path: str,
    parser,
    *,
    skip_until_header: str | None = None,
) -> tuple[int, int]:
    """Read a contact CSV; insert new contacts, MERGE existing ones (Decision 5).

    On a LinkedIn-URL match, upgrade the source label if the incoming source
    outranks the stored one (recruiter > alumni > linkedin) and backfill the
    richer fields (title, vertical_fit, notes, position, email). Returns
    (inserted, merged).
    """
    rows = csvport.read_csv(path, skip_until_header=skip_until_header)
    now = datetime.now(timezone.utc).isoformat()
    inserted = merged = 0
    for raw in rows:
        c = parser(raw)
        linkedin_url = (c.get("linkedin_url") or "").strip()
        existing_id = find_duplicate_contact(db_path, linkedin_url) if linkedin_url else None

        if existing_id is not None:
            if _merge_contact(db_path, existing_id, c):
                merged += 1
            continue

        with cursor(db_path) as cur:
            cur.execute(
                "INSERT INTO contacts "
                "(name, company, email, linkedin_url, degree, source, "
                " title, vertical_fit, notes, position, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.get("name", ""), c.get("company", ""), c.get("email", ""),
                 linkedin_url, c.get("degree", 1), c["source"],
                 c.get("title"), c.get("vertical_fit"), c.get("notes"),
                 c.get("position"), now),
            )
        inserted += 1
    return inserted, merged


def _merge_contact(db_path: str, contact_id: int, incoming: dict) -> bool:
    """Upgrade source label + backfill richer fields on an existing contact.
    Returns True if anything changed."""
    with cursor(db_path) as cur:
        row = cur.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        cur_source = row["source"]
        new_source = incoming["source"]
        # Upgrade source only if incoming outranks the stored label.
        source = (new_source if _SOURCE_PRIORITY.get(new_source, 0) > _SOURCE_PRIORITY.get(cur_source, 0)
                  else cur_source)
        # Backfill: prefer a non-empty incoming value, else keep what's stored.
        def pick(field):
            return (incoming.get(field) or row[field]) or None
        new_vals = (
            source,
            pick("email") or "",
            pick("title"),
            pick("vertical_fit"),
            pick("notes"),
            pick("position"),
        )
        old_vals = (row["source"], row["email"], row["title"],
                    row["vertical_fit"], row["notes"], row["position"])
        if new_vals == old_vals:
            return False
        cur.execute(
            "UPDATE contacts SET source=?, email=?, title=?, vertical_fit=?, "
            "notes=?, position=? WHERE id=?",
            (*new_vals, contact_id),
        )
    return True
