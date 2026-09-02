"""Socket Mode listener — receives Block Kit button clicks over a WebSocket.

The always-on side of the button-approval design (E-D3). Uses the app-level
token (xapp-) to open an OUTBOUND WebSocket to Slack, so interactivity works
with no public endpoint / firewall hole. On a click it applies the pure
transition (approval.apply_action) and updates the message in place so the
`#banks` thread reflects reality.

Run it (test workspace, needs the process to stay up):

    python -m banks.socket_listener

This is the one long-lived process Banks needs for buttons; in production it is
the Phase-G box's service. If it is down, a click errors in Slack — the emoji
reaction fallback (banks.reactions poller) still catches approvals later.
"""

from __future__ import annotations

import re

from .approval import ButtonAction, apply_action
from .attack_queue import (
    QUEUE_ACTION_DONE,
    QUEUE_ACTION_SKIP,
    QUEUE_ACTION_SNOOZE,
)
from .commands import handle_command, route
from .config import BanksConfig, load_config
from .halt import clear_halt, is_halt_command, is_unhalt_command, set_halt
from .queue_actions import mark_done, skip_item, snooze_item
from .revisions import (
    apply_revision,
    clear_pending_revision,
    get_pending_revision,
    set_pending_revision,
)

# Cancelling an active Revise. This was an exact-match set, so "forget it",
# "scratch that", even "Cancel." with a full stop were fed to apply_revision as
# the *edit instruction* — Banks would try to rewrite the draft to say "forget
# it" (found 2026-09-02). Matched as a regex over the whole message now:
# punctuation-tolerant, and covers the natural ways someone backs out.
_CANCEL_WORDS = {"cancel", "nevermind", "never mind", "stop revising"}

_CANCEL_RE = re.compile(
    r"^\s*(?:"
    r"(?:nah|no|nope|actually|umm?|ok|okay)?[\s,]*"
    r"(?:cancel|never\s?mind|forget\s+it|forget\s+that|scratch\s+that|"
    r"skip\s+(?:it|that|this)(?:\s+one)?|leave\s+it(?:\s+as\s+is)?|"
    r"don'?t\s+bother|no\s+need|stop\s+revising|stop\s+it|undo|abort|"
    r"as\s+you\s+were|belay\s+that)"
    r"|(?:actually\s+)?(?:no|nope|nah)"   # bare negation, anchored: "actually no",
    r")[\s.!,]*$",                          # but NOT "no more than 3 sentences"
    re.IGNORECASE,
)


def is_cancel_revision(text: str) -> bool:
    """True if this message backs out of an active Revise rather than instructing it.

    Bias: when unsure, treat it as an INSTRUCTION, not a cancel. Wrongly
    cancelling loses Josh's typed edit; wrongly revising is visible and
    re-doable, and the redraft is never sent without another approval.
    """
    t = (text or "").strip()
    if t.lower() in _CANCEL_WORDS:
        return True
    return bool(_CANCEL_RE.match(t))


def _debug_log(msg: str) -> None:
    """Print only when BANKS_LISTENER_DEBUG is set. Includes message + reply text,
    so enabling it puts Slack chat contents in the server log — opt-in by design."""
    import os
    if os.environ.get("BANKS_LISTENER_DEBUG"):
        print(f"[listener] {msg}", flush=True)


def is_authorized(cfg: BanksConfig, user_id: str) -> bool:
    """Approver lock (Q13): only a configured approver may drive actions.

    BANKS_APPROVER_USER_ID takes a comma-separated list, so Josh and whoever
    operates Banks alongside him can both act. Approve triggers a real Relay
    send, so every id here is a genuine authority grant — keep the list short.

    An empty/unset value means "allow anyone" (test workspaces with one member);
    `run()` refuses to start live in that state.
    """
    approvers = cfg.approver_ids
    if not approvers:
        return True
    return user_id in approvers


def classify_incoming(text: str, has_pending_revision: bool) -> str:
    """Listener precedence (Q23), pure so it's unit-testable without Slack.

    (1) halt/kill ALWAYS first — a shadowed kill switch is a broken kill switch.
    (2) if the user has a pending Revise (tapped the button) → revise; their
        next message is the instruction. Button-driven, since Slack one-level
        threading can't target a card by reply thread_ts.
    (3) any other non-empty message → command.
    (4) otherwise ignore.
    """
    if is_halt_command(text or ""):
        return "halt"
    if has_pending_revision:
        return "revise"
    if (text or "").strip():
        return "command"
    return "ignore"


def _handle_action(cfg: BanksConfig, web, payload: dict, llm=None, chat=None) -> None:
    actions = payload.get("actions") or []
    if not actions:
        return
    act = actions[0]
    action_id = act.get("action_id")
    draft_ref = act.get("value")
    user_id = (payload.get("user") or {}).get("id", "")
    channel = (payload.get("channel") or {}).get("id")
    ts = (payload.get("message") or {}).get("ts")

    # Single-approver lock: ignore clicks from anyone but Josh (Q13).
    if not is_authorized(cfg, user_id):
        print(f"[listener] IGNORED click action={action_id!r} from user={user_id!r} "
              f"(approvers={cfg.approver_ids!r}) — not an approver", flush=True)
        return
    print(f"[listener] click action={action_id!r} draft_ref={draft_ref!r} "
          f"by user={user_id!r}", flush=True)

    # Queue-only actions (Q3/Q4): Skip / Snooze / Mark-done live in queue_actions,
    # not approval.ButtonAction. Handle them before the ButtonAction parse.
    if action_id in (QUEUE_ACTION_SKIP, QUEUE_ACTION_SNOOZE, QUEUE_ACTION_DONE):
        status = _handle_queue_action(cfg, action_id, draft_ref)
        if channel and ts:
            _update_card(web, channel, ts, status, draft_ref, user_id)
        return

    try:
        button = ButtonAction(action_id)
    except ValueError:
        return  # not one of ours

    # Revise (Q4): the button identifies the exact card, so we capture the draft
    # in a per-user pending slot; the user's NEXT message is the instruction.
    # (Replaces the broken thread-reply targeting — Slack threads one level deep.)
    if button is ButtonAction.REVISE:
        set_pending_revision(cfg.db_path, user_id, draft_ref)
        if channel and ts:
            _update_card(
                web, channel, ts,
                "✍️ *Revising* — reply with your change (e.g. `shorter`, "
                "`less formal`, `stronger hook`). Say `cancel` to stop.",
                draft_ref, user_id,
            )
        return

    # is_outbound is read from the draft's send_intent (R-D3), set at draft time
    # by propose(). Approve on an email:* intent enqueues Relay; none:internal
    # just acknowledges.
    result = apply_action(cfg.db_path, button, draft_ref, user_id)
    print(f"[listener] {button.value} applied draft_ref={draft_ref!r} "
          f"enqueue_send={getattr(result, 'enqueue_send', None)}", flush=True)

    # Acknowledge the click BEFORE any slow/fallible follow-on work. Surround
    # generation calls the LLM and Slack; if it raised (anything but ValueError
    # escaped the helper), the card was never updated, Josh saw his tap do
    # nothing, and tapped Approve again — a double approval on a real send.
    if channel and ts:
        _update_card(web, channel, ts, result.status_text, draft_ref, user_id)

    if button is ButtonAction.APPROVE:
        _maybe_generate_surround_pack(cfg, draft_ref, chat, llm)


# ---------------------------------------------------------------------------
# Freezing a company — the only mutation an @banks message can perform
# ---------------------------------------------------------------------------

def _apply_freeze(cfg: BanksConfig, cmd, user_id: str) -> str:
    """Validate, then either freeze or ask first.

    Two real failures this closes (both seen live 2026-09-02):
      - "replied Evolve stop chasing them" wrote a freeze for the company
        "evolve stop chasing them" and reported "🧊 Froze Evolve". A freeze row
        existed; the actual company kept its cadence.
      - Soft phrasings ("put a pin in Acme") matched nothing, fell through to the
        read-only QA layer, and Josh got a conversational reply while follow-ups
        continued.

    So: never freeze a company Banks doesn't track, and never freeze on an
    inferred intent without a yes.
    """
    from .commands import handle_command, is_pronoun_reference
    from .confirm import (confirmation_prompt, resolve_known_company,
                          set_pending_confirmation, unknown_company_reply)

    # "ok stop chasing them" matches the regex with company="them". The pronoun
    # has no antecedent here (mentions are routed per-message), so ask rather
    # than reporting "I don't track them" — or worse, freezing something.
    if is_pronoun_reference(cmd.company):
        return ("Which company should I stop chasing? "
                "Name it and I'll freeze the follow-ups there.")

    slug, suggestions = resolve_known_company(cfg.db_path, cmd.company)
    if slug is None:
        return unknown_company_reply(cmd.company, suggestions)

    if cmd.source == "llm":
        # Inferred from paraphrase — propose, don't write.
        set_pending_confirmation(cfg.db_path, user_id, cmd.intent, slug, cmd.raw)
        return confirmation_prompt(cmd.intent, slug)

    return handle_command(cfg.db_path, type(cmd)(cmd.intent, slug, raw=cmd.raw,
                                                 source=cmd.source))


def _resolve_pending_confirmation(cfg: BanksConfig, web, text: str, user_id: str,
                                  channel: str | None, thread_ts: str | None) -> bool:
    """Handle a yes/no answering an outstanding freeze proposal.

    Returns True if this message was consumed. An unrecognised reply is NOT
    consent: the proposal is dropped and the message continues to normal
    routing, so Josh is never frozen by ambiguity.
    """
    from .commands import Command, handle_command
    from .confirm import (clear_pending_confirmation, get_pending_confirmation,
                          read_confirmation)

    pending = get_pending_confirmation(cfg.db_path, user_id)
    if not pending:
        return False

    verdict = read_confirmation(text)
    if verdict is None:
        clear_pending_confirmation(cfg.db_path, user_id)
        return False           # not an answer — treat as a new message

    clear_pending_confirmation(cfg.db_path, user_id)
    if verdict is False:
        reply = f"Understood — leaving *{pending['company']}* running."
    else:
        reply = handle_command(cfg.db_path, Command(
            pending["intent"], pending["company"], raw=pending["raw"]))
    # Mutations must leave an audit line — a freeze applied with no log entry is
    # invisible in the journal (gap found in live testing 2026-09-02).
    print(f"[listener] confirmation {'accepted' if verdict else 'declined'} "
          f"intent={pending['intent']!r} company={pending['company']!r} "
          f"by user={user_id!r}", flush=True)
    if reply and channel:
        web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=reply)
    return True


def _maybe_generate_surround_pack(cfg: BanksConfig, draft_ref: str, chat, llm) -> None:
    """Approving a Tier A/B 'pursue this role?' card must produce its surround
    pack (MOD-03) — otherwise the click acknowledges the card and nothing else
    happens. Only 'opportunity' packets carry a linked opportunity (see
    opportunities.source_packet_id, set by intake._surface_opportunity); other
    kinds (property inquiries, outreach lanes themselves) are no-ops here.

    Idempotent: skipped if the opportunity already has any outreach_lanes rows,
    so a duplicate Slack delivery of the same click can't double-post lanes.
    """
    if chat is None:
        return
    from .opportunity import load_career_facts
    from .store import cursor
    from .surround import generate_surround_pack

    with cursor(cfg.db_path) as cur:
        opp = cur.execute(
            "SELECT id FROM opportunities WHERE source_packet_id = ?", (draft_ref,)
        ).fetchone()
        if not opp:
            return
        existing = cur.execute(
            "SELECT 1 FROM outreach_lanes WHERE opportunity_id = ? LIMIT 1",
            (opp["id"],),
        ).fetchone()
    if existing:
        return

    try:
        generate_surround_pack(cfg.db_path, opp["id"], load_career_facts(), chat, llm)
    except Exception as exc:
        # Catch everything, not just ValueError: an LLM timeout or Slack error
        # must not escape into the click handler. And TELL Josh — a silent
        # failure means he approves a Tier A and simply never gets the pack.
        print(f"[listener] surround pack error: {exc!r}", flush=True)
        if chat is not None:
            reason = ("your career facts are empty — add them to "
                      "career-facts.md and I can draft the pack"
                      if isinstance(exc, ValueError) and "career" in str(exc).lower()
                      else f"{type(exc).__name__}: {exc}")
            try:
                chat.post(f"⚠️ Approved, but I couldn't build the surround pack — {reason}")
            except Exception:
                pass  # never let the notification itself break the click


def _handle_queue_action(cfg: BanksConfig, action_id: str, draft_ref: str) -> str:
    """Skip / Snooze / Mark-done (Q3/Q4) → queue_actions; returns status text."""
    if action_id == QUEUE_ACTION_SKIP:
        skip_item(cfg.db_path, draft_ref)
        return "⏭️ *Skipped* — off today's queue (won't auto-return)."
    if action_id == QUEUE_ACTION_SNOOZE:
        snooze_item(cfg.db_path, draft_ref)  # default 1 day
        return "😴 *Snoozed* — back in tomorrow's queue."
    mark_done(cfg.db_path, draft_ref)
    return "✔️ *Marked done* — logged as a completed touch."


def _update_card(web, channel: str, ts: str, status_text: str,
                 draft_ref: str, user_id: str) -> None:
    web.chat_update(
        channel=channel, ts=ts, text=status_text,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": status_text}},
            {"type": "context",
             "elements": [{"type": "mrkdwn",
                           "text": f"draft_ref `{draft_ref}` · by <@{user_id}>"}]},
        ],
    )


def _handle_message(cfg: BanksConfig, web, llm, chat, event: dict) -> None:
    """Dispatch a message by the ONE precedence rule: classify_incoming decides
    halt → revise → command → ignore, this function only acts on the verdict.
    (Previously the precedence was re-derived inline here + in _on; now the pure
    classifier is the single source of the rule and the live path exercises it.)
    """
    # Text can arrive top-level OR, for an EDITED message, under `message`
    # (subtype 'message_changed'). The kill switch must see both — a halt typed
    # as an edit is still a halt (a shadowed kill switch is a broken one).
    is_bot = bool(event.get("bot_id"))
    text = event.get("text") or (event.get("message") or {}).get("text") or ""
    user_id = event.get("user", "")
    channel = event.get("channel")

    # HALT FIRST, on the broadest surface — any human message including edits.
    # (Skip the bot's own messages so a halt-confirmation can't re-trigger halt.)
    if is_halt_command(text):
        if is_bot:
            return
        set_halt(reason=f"operator command: '{text.strip()}'")
        _debug_log(f"msg from {user_id!r}: {text!r} -> GLOBAL HALT")
        web.chat_postMessage(
            channel=cfg.slack_channel_id or "",
            text=("🛑 *Banks halted — ALL jobs suspended.* Nothing will send until "
                  "you say `resume`. (To stop just one company instead, say "
                  "`stop chasing <company>` or `replied <company>`.)"),
        )
        return

    # UN-HALT — approver-only (re-enabling sends must not be open to anyone).
    if is_unhalt_command(text):
        if is_bot:
            return
        if not is_authorized(cfg, user_id):
            print(f"[listener] IGNORED resume from user={user_id!r} "
                  f"(approvers={cfg.approver_ids!r})", flush=True)
            return
        clear_halt()
        _debug_log(f"msg from {user_id!r}: {text!r} -> RESUME (halt cleared)")
        web.chat_postMessage(
            channel=cfg.slack_channel_id or "",
            text="✅ *Banks resumed.* Standing jobs run again on the next tick.",
        )
        return

    # Non-halt: ignore the bot's own messages + non-plain subtypes (edits, joins,
    # etc.) — a revision/command loop otherwise is inevitable.
    if is_bot or event.get("subtype"):
        return

    # A pending Revise (Josh-only) captures this message as the instruction.
    pending = (get_pending_revision(cfg.db_path, user_id)
               if is_authorized(cfg, user_id) else None)

    verdict = classify_incoming(text, pending is not None)

    if verdict == "revise":
        if is_cancel_revision(text):
            clear_pending_revision(cfg.db_path, user_id)
            if channel:
                web.chat_postMessage(channel=channel, text="✍️ Revision cancelled.")
            return
        from .opportunity import load_career_facts
        res = apply_revision(cfg.db_path, pending, text, load_career_facts(), llm, chat)
        clear_pending_revision(cfg.db_path, user_id)
        if res.get("ok"):
            msg = f"✍️ Revised: {text.strip()}"
        elif res.get("reason") == "embellishment":
            msg = "✍️ Skipped — that would add a claim not in your resume."
        elif res.get("reason") == "no_pending_draft":
            msg = "✍️ No draft awaiting revision — tap *Revise* on a card first."
        else:
            msg = f"✍️ Couldn't revise: {res.get('reason')}"
        if channel:
            web.chat_postMessage(channel=channel, text=msg)
        return

    # verdict == "command" or "ignore" → do nothing.
    # Untagged messages are silent by design: all Q&A/commands now require an
    # @banks mention (routed via _handle_app_mention). Only the button-triggered
    # revision flow (above) and the halt/unhalt safety fallback act untagged.
    _debug_log(f"msg from {user_id!r}: {text!r} -> ignored (no @banks tag)")


# ---------------------------------------------------------------------------
# Slack CSV upload (MOD-01) — Josh drags a Simplify export into #banks-jobs.
# Requires the `files:read` scope on the Banks Slack app (CTO/client item).
# ---------------------------------------------------------------------------

def _is_csv(f: dict) -> bool:
    return (f.get("filetype") == "csv"
            or (f.get("name") or "").lower().endswith(".csv"))


def should_ingest_file(cfg: BanksConfig, event: dict) -> bool:
    """Pure gate: ingest a dropped file only if it's Josh's CSV in the jobs
    channel (single-approver + channel lock + skip bot files)."""
    if event.get("bot_id"):
        return False
    files = event.get("files") or []
    if not files or not any(_is_csv(f) for f in files):
        return False
    if not is_authorized(cfg, event.get("user", "")):
        return False
    if event.get("channel") != cfg.slack_jobs_channel_id:
        return False
    return True


def _is_jd_file(f: dict) -> bool:
    name = (f.get("name") or "").lower()
    ft = f.get("filetype") or ""
    return (ft == "pdf" or name.endswith(".pdf")
            or ft == "docx" or name.endswith(".docx")
            or ft == "txt" or name.endswith(".txt")
            or _is_csv(f))


def should_ingest_mention_file(cfg: BanksConfig, event: dict) -> bool:
    """Gate for @banks-tagged file uploads (app_mention event).

    Accepts CSV, PDF, docx, txt from an authorized user. No channel restriction
    (Josh may @banks in any channel). Bot uploads are rejected.
    """
    if event.get("bot_id"):
        return False
    files = event.get("files") or []
    if not files or not any(_is_jd_file(f) for f in files):
        return False
    if not is_authorized(cfg, event.get("user", "")):
        return False
    return True


_CSV_CLASSIFY_SYSTEM = (
    "You classify CSV exports for a job-search tool. "
    "Given the filename and first few rows of a CSV, respond with JSON: "
    '{"type": "<type>"} where <type> is one of: '
    '"simplify" (job applications from Simplify), '
    '"linkedin" (LinkedIn connections export), '
    '"recruiter" (recruiter registry with Title/Vertical Fit columns), '
    '"alumni" (alumni/former-colleague list), '
    '"unknown" (none of the above). '
    "Use only the column names and sample values to decide — no guessing."
)


def _classify_csv(path: str, filename: str) -> str:
    """Ask Haiku what kind of CSV this is. Returns one of: simplify/linkedin/recruiter/alumni/unknown."""
    import csv

    # Read up to 3 data rows (skip blank/preamble lines)
    rows: list[str] = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    rows.append(stripped)
                if len(rows) >= 4:  # header + 3 data rows
                    break
    except Exception:
        return "unknown"

    sample = "\n".join(rows)
    user_prompt = f"Filename: {filename}\n\nFirst rows:\n{sample}"

    try:
        from .llmport import load_llm_port
        llm = load_llm_port()
        result = llm.extract_json(_CSV_CLASSIFY_SYSTEM, user_prompt,
                                  schema_hint='{"type": "string"}')
        return result.get("type", "unknown")
    except Exception:
        return "unknown"


def _handle_file(cfg: BanksConfig, web, chat, event: dict) -> None:
    """Download a dropped CSV, classify it with Haiku, route to the right ingest."""
    import os
    import tempfile

    from .csvport import (LiveCSVPort, parse_linkedin_connection_row,
                          parse_recruiter_row, parse_alumni_row)
    from .intake import ingest_simplify, ingest_contacts
    from .slackfiles import download

    channel = event.get("channel")
    files = event.get("files") or []
    # Non-CSV drop in the jobs channel → tell the user.
    if (not event.get("bot_id")
            and is_authorized(cfg, event.get("user", ""))
            and channel == cfg.slack_jobs_channel_id
            and files and not any(_is_csv(f) for f in files)):
        web.chat_postMessage(channel=channel,
                             text="📎 That's not a CSV — drop a Simplify, LinkedIn connections, recruiter, or alumni export.")
        return

    if not should_ingest_file(cfg, event):
        return

    for f in files:
        if not _is_csv(f):
            continue
        name = f.get("name") or "export.csv"
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            info = web.files_info(file=f.get("id"))
            url = (info.get("file") or {}).get("url_private_download")
        try:
            data = download(url, cfg.slack_bot_token or "")
            tmp = os.path.join(tempfile.mkdtemp(), name)
            with open(tmp, "wb") as fh:
                fh.write(data)

            csv_type = _classify_csv(tmp, name)

            if csv_type == "linkedin":
                inserted, merged = ingest_contacts(
                    cfg.db_path, LiveCSVPort(), tmp,
                    parse_linkedin_connection_row,
                    skip_until_header="First Name",
                )
                web.chat_postMessage(
                    channel=channel,
                    text=(f"👥 Imported LinkedIn connections *{name}* — "
                          f"{inserted} new contacts, {merged} merged."),
                )
                continue

            if csv_type == "recruiter":
                inserted, merged = ingest_contacts(
                    cfg.db_path, LiveCSVPort(), tmp,
                    parse_recruiter_row,
                )
                web.chat_postMessage(
                    channel=channel,
                    text=(f"👥 Imported recruiter list *{name}* — "
                          f"{inserted} new, {merged} merged."),
                )
                continue

            if csv_type == "alumni":
                inserted, merged = ingest_contacts(
                    cfg.db_path, LiveCSVPort(), tmp,
                    parse_alumni_row,
                )
                web.chat_postMessage(
                    channel=channel,
                    text=(f"👥 Imported alumni list *{name}* — "
                          f"{inserted} new, {merged} merged."),
                )
                continue

            if csv_type == "unknown":
                web.chat_postMessage(
                    channel=channel,
                    text=(f"⚠️ Couldn't identify *{name}* — drop a Simplify, "
                          f"LinkedIn connections, recruiter, or alumni CSV."),
                )
                continue

            # simplify (default)
            res = ingest_simplify(cfg.db_path, LiveCSVPort(), tmp, chat)
        except Exception as exc:
            web.chat_postMessage(
                channel=channel,
                text=f"⚠️ Couldn't import *{name}* — {exc}",
            )
            continue
        total = res.ingested + res.duplicates + res.excluded
        web.chat_postMessage(
            channel=channel,
            text=(f"📥 Imported *{name}* — {total} rows: {res.ingested} new, "
                  f"{res.duplicates} duplicates, {res.excluded} excluded. "
                  f"{res.held} held for enrichment; Tier A/B appear in your queue "
                  f"as industry resolves."),
        )


def _handle_mention_file(cfg: BanksConfig, web, chat, event: dict) -> None:
    """Handle a file dropped in a @banks mention (app_mention event).

    Accepts: CSV (→ ingest_simplify), PDF/docx/txt (→ docparse + manual_intake).
    One receipt per file, multiple files in one drop are all processed.
    """
    import os
    import tempfile

    from .slackfiles import download

    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    files = event.get("files") or []

    for f in files:
        if not _is_jd_file(f):
            continue
        name = f.get("name") or "upload"
        url = f.get("url_private_download") or f.get("url_private")
        if not url:
            info = web.files_info(file=f.get("id"))
            url = (info.get("file") or {}).get("url_private_download")
        try:
            data = download(url, cfg.slack_bot_token or "")
        except Exception as exc:
            if channel:
                web.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                     text=f"⚠️ Couldn't download *{name}*: {exc}")
            continue

        if _is_csv(f):
            # CSV → Simplify intake (same as legacy message path)
            from .csvport import LiveCSVPort
            from .intake import ingest_simplify
            tmp = os.path.join(tempfile.mkdtemp(), name)
            with open(tmp, "wb") as fh:
                fh.write(data)
            try:
                res = ingest_simplify(cfg.db_path, LiveCSVPort(), tmp, chat)
            except Exception as exc:
                if channel:
                    web.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=f"⚠️ Couldn't import *{name}*: {exc}",
                    )
                continue
            total = res.ingested + res.duplicates + res.excluded
            if channel:
                web.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=(f"📥 Imported *{name}* — {total} rows: {res.ingested} new, "
                          f"{res.duplicates} duplicates, {res.excluded} excluded. "
                          f"{res.held} held for enrichment."),
                )
        else:
            # PDF / docx / txt → docparse + manual_intake
            from .docparse import TooLittleText, extract_text
            from .llmport import load_llm_port
            from .manual_intake import ingest_manual
            try:
                text = extract_text(data, name)
            except TooLittleText as exc:
                if channel:
                    web.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=(f"📄 *{name}* looks like a scanned image — I couldn't "
                              f"extract text from it. ({exc}) Please paste the JD text "
                              f"directly instead."),
                    )
                continue
            except ValueError as exc:
                if channel:
                    web.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=f"⚠️ Unsupported file type — {exc}. Use .pdf, .docx, or .txt.",
                    )
                continue
            try:
                result = ingest_manual(cfg.db_path, chat, jd_text=text,
                                       llm=load_llm_port())
                if channel:
                    web.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=(f"📄 Processed *{name}* — "
                              f"{'added to pipeline' if result else 'held for enrichment'}."),
                    )
            except Exception as exc:
                if channel:
                    web.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=f"⚠️ Couldn't process *{name}*: {exc}",
                    )


def _handle_app_mention(cfg: BanksConfig, web, llm, chat, event: dict,
                        bot_user_id: str = "") -> None:
    """Handle an @banks app_mention event.

    Precedence (same order as _handle_message):
    1. halt — @banks stop all
    2. unhalt — @banks resume
    3. file upload — @banks + file(s)
    4. QA question / command — answer via qa.handle_qa_mention
    """
    text = event.get("text") or ""
    user_id = event.get("user", "")
    channel = event.get("channel")
    # Only thread if already in a thread; top-level @banks → reply in main channel
    thread_ts = event.get("thread_ts") or None

    # Strip the @mention prefix before checking intent
    stripped = strip_mention(text, bot_user_id) if bot_user_id else text.strip()

    # 1. halt
    if is_halt_command(stripped):
        if not is_authorized(cfg, user_id):
            return
        set_halt(reason=f"operator command: '{stripped.strip()}'")
        _debug_log(f"@mention from {user_id!r}: halt triggered")
        if channel:
            web.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=("🛑 *Banks halted — ALL jobs suspended.* Nothing will send until "
                      "you say `@banks resume`."),
            )
        return

    # 2. unhalt
    if is_unhalt_command(stripped):
        if not is_authorized(cfg, user_id):
            return
        clear_halt()
        _debug_log(f"@mention from {user_id!r}: resume")
        if channel:
            web.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text="✅ *Banks resumed.* Standing jobs run again on the next tick.",
            )
        return

    # 3. file upload
    if event.get("files") and should_ingest_mention_file(cfg, event):
        try:
            _handle_mention_file(cfg, web, chat, event)
        except Exception as exc:
            print(f"[listener] mention file error: {exc!r}", flush=True)
        return

    if not is_authorized(cfg, user_id):
        print(f"[listener] mention ignored — user {user_id!r} not authorized "
              f"(approvers={cfg.approver_ids!r})", flush=True)
        return
    print(f"[listener] @mention from {user_id!r}: {stripped!r}", flush=True)

    # 3b. An outstanding "did you mean to freeze X?" — this message may be the
    # yes/no. Checked before routing so "yes" isn't read as a fresh question.
    if _resolve_pending_confirmation(cfg, web, stripped, user_id, channel, thread_ts):
        return

    # 4. CONTROL commands (mutations). A freeze stops every follow-up at a
    # company, so it goes through _apply_freeze: the company must be one Banks
    # actually tracks, and an LLM-inferred intent is confirmed before it fires.
    # Read intents fall through to the QA layer's tool-calling.
    cmd = route(cfg.db_path, stripped, llm)
    if cmd.intent in ("stop_company", "replied", "unfreeze_company"):
        reply = _apply_freeze(cfg, cmd, user_id)
        print(f"[listener] control cmd intent={cmd.intent!r} source={cmd.source!r} "
              f"-> {reply!r}", flush=True)
        if reply and channel:
            web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=reply)
        return

    # 5. QA (read-only LLM tool-calling)
    from .qa import handle_qa_mention
    try:
        reply = handle_qa_mention(
            cfg=cfg, db_path=cfg.db_path, text=stripped,
            user_id=user_id, llm=llm, thread_ts=thread_ts,
        )
    except Exception as exc:
        print(f"[listener] qa error: {exc!r}", flush=True)
        reply = "⚠️ Something went wrong — try again."
    print(f"[listener] qa reply: {reply!r}", flush=True)
    if reply and channel:
        web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=reply)


def strip_mention(text: str, bot_user_id: str) -> str:
    """Re-export from qa for use in socket_listener."""
    from .qa import strip_mention as _strip
    return _strip(text, bot_user_id)


def _mentions_bot(event: dict, bot_user_id: str) -> bool:
    """Return True if the event mentions the bot.

    Slack file-share messages often omit the mention from ``text`` and put it
    only in ``blocks[].elements`` as a rich_text user element.  Check both so
    file uploads tagged with @banks are routed correctly regardless of Slack's
    block vs. plain-text choice.

    Also accepts the ``<@UID|display>`` variant that Slack occasionally emits.
    """
    if not bot_user_id:
        text = event.get("text") or ""
        return text.strip().startswith("<@")
    text = event.get("text") or ""
    # Plain-text check — bot_user_id prefix handles <@UID> and <@UID|name>
    if f"<@{bot_user_id}" in text:
        return True
    # Rich-text block check — walk all block elements
    for block in event.get("blocks") or []:
        for element in block.get("elements") or []:
            # rich_text_section wraps the actual inlines
            for inner in element.get("elements") or [element]:
                if inner.get("type") == "user" and inner.get("user_id") == bot_user_id:
                    return True
    return False


def run(cfg: BanksConfig | None = None) -> None:
    cfg = cfg or load_config()
    if not (cfg.slack_bot_token and cfg.slack_app_token):
        raise SystemExit("Need BANKS_SLACK_BOT_TOKEN and BANKS_SLACK_APP_TOKEN.")
    # The kill switch lives in the DB so it reaches the scheduler process (which
    # owns relay_dispatch). This listener does not go through Container.live(),
    # so it must point halt at the DB itself — without this, "@banks stop all"
    # would set a flag only this process can see and nothing would stop sending.
    from .halt import init_halt
    from .store import init_db
    init_db(cfg.db_path)
    init_halt(cfg.db_path)
    import os as _os
    if not cfg.approver_ids and not _os.environ.get("BANKS_SKIP_APPROVER_CHECK"):
        # Fail closed: an unset approver id makes is_authorized() allow anyone
        # (the test-workspace default). A live listener must never inherit that.
        raise SystemExit("Need BANKS_APPROVER_USER_ID set before running live — "
                         "an unset approver id lets any workspace member approve sends.")

    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient

    from .chatport import LiveChatPort
    from .llmport import load_llm_port

    web = WebClient(token=cfg.slack_bot_token)
    sm = SocketModeClient(app_token=cfg.slack_app_token, web_client=web)
    llm = load_llm_port()
    chat = LiveChatPort(cfg)

    # Resolve bot's own user_id once at startup for @mention stripping
    try:
        bot_user_id = web.auth_test()["user_id"]
        print(f"[listener] bot_user_id={bot_user_id!r}", flush=True)
    except Exception as exc:
        bot_user_id = ""
        print(f"[listener] WARNING: auth_test failed — bot_user_id unknown: {exc!r}", flush=True)

    # Slack fires both a `message` and an `app_mention` event for every @banks
    # message (text or file). Both share the same (channel, ts), so we use that
    # as a dedup key — first event wins, second is dropped before any processing.
    _seen_events: set[tuple[str, str]] = set()

    def _event_key(ev: dict) -> tuple[str, str]:
        return (ev.get("channel") or "", ev.get("ts") or ev.get("event_ts") or "")

    def _on(client: SocketModeClient, req: SocketModeRequest) -> None:
        # Ack first (Slack requires a prompt ack), then act.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        import os as _os
        if _os.environ.get("BANKS_LISTENER_DEBUG"):
            _et = (req.payload.get("event") or {}).get("type") if isinstance(req.payload, dict) else None
            print(f"[listener] recv type={req.type} event={_et}", flush=True)

        if req.type == "events_api":
            event = (req.payload.get("event") or {})
            etype = event.get("type")

            # @banks mention (commands, QA, tagged uploads, halt, unhalt)
            if etype == "app_mention":
                key = _event_key(event)
                if key[1] and key in _seen_events:
                    return
                _seen_events.add(key)
                try:
                    _handle_app_mention(cfg, web, llm, chat, event, bot_user_id)
                except Exception as exc:
                    print(f"[listener] mention error: {exc!r}", flush=True)
                return

            if etype != "message":
                return
            # Slack sends ALL @mention messages (text and file) as message events
            # when app_mention is not subscribed or not firing. Check for bot
            # mention in the text and route to _handle_app_mention in both cases.
            if _mentions_bot(event, bot_user_id):
                key = _event_key(event)
                if key[1] and key in _seen_events:
                    return
                _seen_events.add(key)
                try:
                    _handle_app_mention(cfg, web, llm, chat, event, bot_user_id)
                except Exception as exc:
                    print(f"[listener] mention error: {exc!r}", flush=True)
                return
            if event.get("files"):
                key = _event_key(event)
                if key[1] and key in _seen_events:
                    return
                _seen_events.add(key)
                try:
                    _handle_file(cfg, web, chat, event)
                except Exception as exc:
                    print(f"[listener] file error: {exc!r}", flush=True)
                return
            try:
                _handle_message(cfg, web, llm, chat, event)
            except Exception as exc:
                print(f"[listener] message error: {exc!r}", flush=True)
            return

        if req.type == "interactive":
            # Dedup on envelope_id — Slack retries interactive payloads on timeout.
            eid = req.envelope_id or ""
            if eid and eid in _seen_events:
                return
            if eid:
                _seen_events.add(eid)
            try:
                _handle_action(cfg, web, req.payload, llm, chat)
            except Exception as exc:  # never let a click die silently
                print(f"[listener] handler error: {exc!r}", flush=True)

    sm.socket_mode_request_listeners.append(_on)
    sm.connect()
    print("Banks Socket Mode listener connected. Waiting for button clicks… Ctrl-C to stop.")
    from threading import Event
    Event().wait()


if __name__ == "__main__":
    run()
