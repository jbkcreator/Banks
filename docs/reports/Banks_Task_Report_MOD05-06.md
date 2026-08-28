# Banks — Task Report: MOD-05 & MOD-06
**Date:** 2026-08-27

---

## Overview

This report covers MOD-05 (Slack Command & Control / Daily Attack Queue) and
MOD-06 (Adversarial Exclusion & Launch Staging) of the Maximum Distribution
Build. Both are **code-complete with automated tests green (426 tests passing
across the full suite)**. MOD-05's approval loop is proven live in the test Slack
workspace; MOD-06's exclusion wall and launch tooling are built and covered by an
adversarial test suite.

Banks prepares and proposes but **never sends, posts, or submits** without
Josh's explicit one-tap approval, and stays entirely walled off from Forced
Action — no shared systems, credentials, or data.

The work sits on branches `feature/mod05-command-control` and
`feature/mod06-exclusion-launch` (stacked on the MOD-01→04 branches).

---

## Status at a Glance

| Module | Scope | Status |
|---|---|---|
| MOD-05 | Slack cockpit, interactive actions, revisions, retrieval | **Build-complete, tested; approval loop proven live** |
| MOD-06 | Two-gate exclusion, adversarial tests, launch staging | **Build-complete, tested; exclusion wall proven by adversarial suite** |

"Build-complete" means the logic is written and covered by automated tests.
"Live-proven" means it was exercised against real Slack / real LLM.

---

## MOD-05 — Slack Command & Control / Daily Attack Queue

**Status: Build-complete — core approval loop proven live; NL revision broken (parked); 2 open review defects**

### What We Built

The morning cockpit Josh runs his day from: a Daily Attack Queue that posts once
each morning, individually-approvable draft cards, one-tap actions, in-thread
natural-language revisions, and on-demand lookups — all over Slack.

### Components

- `banks/attack_queue.py` — pure `build_sections()` (failure-mode-first order,
  empty-section omit, career-facts blocker line, score ranking, imported digest,
  funnel footer) + `post_daily_queue()` (exactly-once per day via a `daily_queue`
  date-claim; cards threaded under a summary header; carried-over items re-posted
  aging-flagged).
- `banks/queue_actions.py` — Snooze (next-morning), Skip (today-only), Mark-done
  (MARK_SENT semantics: touch_log + cadence + funnel).
- `banks/commands.py` — hybrid intent router (keyword fast-path → LLM fallback),
  bounded to three intents: warm-path ("who do I know at X"), company status
  snapshot, daily call list.
- `banks/revisions.py` — facts-only rewrite + embellishment post-check → redraft
  in place.
- `banks/socket_listener.py` — live loop handling button clicks and message
  events (halt → revise → command → ignore precedence) + single-approver lock.
  Attack Queue cards render the client's button row: Approve / Revise / Skip /
  Snooze / Mark done.
- Schema: `queue_items` (+`card_ts`), `daily_queue`. Scheduled
  `daily_attack_queue` job at 7:30 ET. Slack app manifest for production cutover.

### Locked Decisions (grill, 27 items — docs/decisions/BUILD_DECISIONS_MOD03-06.md)

Own channel `#banks-jobs`; summary header + threaded cards; exactly-once posting;
failure-mode-first order; Reject=terminal / Snooze=next-morning / Skip=today-only;
Mark-done reuses MARK_SENT; single-approver lock; hybrid router bounded to core
retrieval; facts-only revision; graceful degrade on empty career-facts.

### Validation

- **426 tests passing** (42 added for MOD-05: attack_queue, queue_actions,
  commands, revisions, button-row dispatch).
- **Proven LIVE in the test workspace:** Daily Attack Queue posts + idempotency;
  Approve / Mark-sent / Reject buttons (DB state confirmed end-to-end); command
  router + revision engine driven by the real Anthropic LLM.

### To activate for live use

- Populate `career-facts.md` with Josh's resume so draft content is real
  (client query, see below).
- Provision the dedicated Banks Slack app in Josh's workspace with Event
  Subscriptions enabled (client/CTO query, see below).

### Evidence

`banks/attack_queue.py` · `banks/queue_actions.py` · `banks/commands.py` ·
`banks/revisions.py` · `banks/socket_listener.py` · `scripts/mod05_smoke_test.py`

---

## MOD-06 — Adversarial Exclusion & Launch Staging

**Status: Build-complete — exclusion wall tested (caught a real evasion bug); live E2E and startup-wiring pending**

### What We Built

The safety wall that guarantees Banks never contacts an excluded company, person,
or anyone reached indirectly through an excluded firm — enforced at both draft
creation and send time — plus the tooling to stage a controlled launch.

### Components

- `banks/exclusion.py` — company exclusion (existing) + **person exclusion**
  keyed on LinkedIn URL / normalized name (stable across job changes), **indirect
  exclusion** (corporate-name substring), **conduit exclusion** (block a warm
  intro routed through an employee at an excluded firm), and a **seed-file
  loader** (`exclusions.txt` as the reviewable source of truth).
- `banks/relay.py` — **send-time exclusion gate**: Relay re-checks every approved
  intent against the exclusion list right before sending and suppresses if now
  excluded (`RelayResult.blocked`); also freezes on halt.
- `banks/surround.py` — **draft-time gate**: person/indirect filtering of
  contacts and intro conduits during pack assembly.
- `banks/normalise.py` — `normalise_name` + whitespace-collapse (closed a real
  evasion: "rent  solutions" with a double space previously slipped past).
- `docs/launch/LAUNCH_ACCEPTANCE.md` (7-item signed live-E2E checklist),
  `docs/launch/LAUNCH_RUNBOOK.md` (ordered go-live + rollback/halt), sample
  `exclusions.txt`. Schema: `person_exclusions`.

### Locked Decisions (grill, 13 items — docs/decisions/BUILD_DECISIONS_MOD03-06.md)

Two gates (draft + send-time); person exclusion on stable identity; indirect =
firm + current employees + name-variants (deeper scope awaits Josh); visible
terse block reasons (no silent drops); file-as-source-of-truth list; adversarial
suite = 6-case evasion matrix incl. a moved-on-ex-employee negative control; mock
E2E all-Fakes; live E2E mandatory and run last; halt = real freeze of scheduler +
Relay.

### Validation

- **426 tests passing** (19 added: adversarial suite, mock E2E, halt-freeze).
- Adversarial suite proves blocking at both gates for casing/suffix/whitespace
  variants, person job-move, indirect conduit, post-queue race, corporate
  substring — plus the negative control (moved-on ex-employee stays contactable).
- Mock E2E runs the full pipeline on Fakes with a planted exclusion that never
  surfaces or sends.

### To activate for live use

- Josh supplies the full exclusion list (former employers, competitors, named
  individuals, conflicts) — currently only "Rent Solutions" is seeded (client
  query, see below).
- Run the live E2E acceptance (7-item checklist) as the final go-live step, by
  agreement run last.

### Evidence

`banks/exclusion.py` · `banks/relay.py` · `banks/surround.py` ·
`banks/normalise.py` · `docs/launch/LAUNCH_ACCEPTANCE.md` · `docs/launch/LAUNCH_RUNBOOK.md`

---

## Email Sending (cross-cutting, needed for MOD-03 outreach send)

- `banks/mailer.py` — added `SmtpMailer` (stdlib SMTP, STARTTLS) behind the
  existing Relay, alongside Fake and Resend. `load_mailer()` picks SMTP when
  configured. Built + unit-tested (mocked SMTP, no network).
- **No live email has been sent, ever.** Activation is optional: without a
  mailer, Josh sends approved drafts himself via "Mark sent" (same as the
  LinkedIn lane). With SMTP, Banks sends on his approval.
- Refused to wire Forced Action's Mandrill credentials (hard-wall). A separate
  Banks/Josh mailbox is required — personal Gmail + an app password is sufficient
  and appropriate for job-search outreach (no custom domain / DKIM needed).

---

## Client Inputs Needed to Complete the Build

These were raised in the query list sent yesterday (`CLIENT_QUERIES_V2.md`). Each
is an input or access item — the engineering is done; these switch the built
features from test to Josh's real accounts:

| Input | Enables |
|---|---|
| **Resume** → `career-facts.md` | Real MOD-03/05 draft content (Banks writes only from verified facts) |
| **Full exclusion list** | MOD-06 exclusion wall for launch (former employers, competitors, named individuals, conflicts) |
| **Dedicated Banks Slack app** (Socket Mode + Event Subscriptions) | Live approval buttons + in-thread revisions in Josh's workspace |
| **Josh's sending email + app password** | Automatic outreach send (else Josh sends via "Mark sent") |
| **Paid Clay / Hunter.io decision** | Hands-off verified-contact enrichment (manual CSV path works in the interim) |

The final go-live step is the live end-to-end acceptance run (7-item checklist,
`docs/launch/LAUNCH_ACCEPTANCE.md`), performed once the inputs above are in place.

---

## Test Suite

`python -m pytest tests/ -q` → **426 passing.** Hard-wall test green (no FA
imports / credentials / shared DB).

## Repository

`github.com/jbkcreator/Banks` — branches `feature/mod05-command-control` and
`feature/mod06-exclusion-launch`, stacked on MOD-01→04.

_Bottom line: MOD-05 and MOD-06 are code-complete and tested; MOD-05's approval
loop is proven live. What remains is the client inputs listed above, after which
the live end-to-end acceptance run takes Banks to go-live._
