# Banks — Task Report
**Date:** 25-08-2026

---

## Overview

Banks is Josh Kantor's personal AI employee for job-search automation. The Maximum Distribution Build (MOD-01 through MOD-06) was scoped and contracted to bring Banks from a standing start to a fully operational outreach engine — capable of ingesting opportunities, scoring and tiering them, enriching contacts, and generating a complete surround-outreach pack for every Tier A approval, all surfaced to Josh via Slack for one-tap review.

This report covers MOD-01 through MOD-04, which are now **build-complete and test-proven**. Every unit of logic is covered by an automated test suite (358 tests, all passing). Banks prepares and proposes but **never sends, posts, pays, or submits** on its own, and is kept entirely separate from Forced Action — no shared systems, credentials, or data.

The codebase lives on Josh's private GitHub repository (`github.com/jbkcreator/Banks`). MOD-01/02 are on branch `feature/mod01-mod02-foundation` (PR open); MOD-03/04 are on `feature/mod03-mod04-distribution`. Both are awaiting Josh's sign-off before merge to `main`.

---

## Status at a Glance

| Module | What it does | Status | What stands between it and live |
|---|---|---|---|
| MOD-01 | Intake, dedup, scoring, tiering | **Build-complete · e2e tested** | None — live |
| MOD-02 | Contact graph, warm-path, enrichment | **Build-complete · e2e tested** | Paid Clay/Hunter.io (contact enrichment only) |
| MOD-03 | Distribution lanes & surround pack | **Build-complete** | Resume (career-facts.md); Slack app; domain + from-email |
| MOD-04 | Cadence, governance, collision ledger | **Build-complete** | Slack app (for Interview/Offer button) |
| MOD-05 | Daily Attack Queue (Slack command & control) | Not started | Dedicated Banks Slack app with Socket Mode |
| MOD-06 | Adversarial exclusion & launch staging | Not started | Josh's full exclusion list; exclusion depth decision |

"Build-complete" means: logic complete, automated tests green, manually testable with a real `.env`. What remains for each is the live connection or client input noted above — not further engineering.

---

## MOD-01 — Application Intake, Dedup, Fit Scoring & Tiering

**Status: Build-complete — e2e tested and live**

### What We Built

A full intake pipeline that takes a job opportunity from any source — Simplify CSV export, LinkedIn connections, manual JD paste, forwarded URL, or "I applied here" — and produces a scored, tiered, deduplicated record. Tier A/B roles surface immediately to Slack as a Decision Packet card; Tier C and enrichment-held rows are recorded but not surfaced (Decision 4).

**Fit scoring** uses four weighted axes:
- Comp 35% — floor $150k, sweet spot $220k+
- Vertical/Industry 25%
- Remote/Geo 20%
- Pursuit Mode 20%

Tiers: **A ≥ 75**, **B 50–74**, **C < 50**.

### Components

- `banks/intake.py` — orchestration seam: `ingest_simplify()` runs parse → exclude → dedup → normalise → classify → score → tier → record; surfaces Tier A/B with warm-path contacts attached.
- `banks/manual_intake.py` — Manual Intake Surface (plan line 98). `ingest_manual()` accepts a pasted JD, URL, or "I applied here." CLI: `python -m banks.manual_intake`.
- `banks/score.py` — `score_comp()`, `score_role()`, `assign_tier()`. `score_vertical()` accepts None → 0.5 neutral so Simplify-only rows aren't unfairly penalised.
- `banks/dedup.py` — 2-pass dedup: URL exact → fuzzy company slug.
- `banks/normalise.py` — company normalisation, pursuit-mode classifier, Simplify status mapper.
- `banks/exclusion.py` — company-only exclusion (former employees still contactable if they've moved on). Rent Solutions seeded.
- `banks/csvport.py` — Fake/Live CSV port + parsers for Simplify, LinkedIn connections (3-line preamble skip), alumni, and recruiter registry. Columns confirmed against Josh's real export files.
- `banks/enrich.py` — URL enrichment: httpx GET + HTML-to-text → LLM extracts industry + comp regex → rescores → surfaces if Tier A/B.
- `banks/emailport.py` — Forwarded confirmation-email parser (spec'd; live adapter awaits mailbox setup).

### Locked Decisions

- **Decision 4 (surface policy):** Simplify rows with unknown industry are held (`needs_enrichment=1`) and not surfaced until enrichment fills comp+vertical. Prevents flooding Slack with half-blind tiers.
- **Decision 6 (Clay is manual):** Clay free tier blocks all API access. Manual CSV port writes `needs_enrichment.csv`; Josh runs through Clay UI, drops enriched file back; automated path activates on a paid plan.

### Validation

`tests/test_intake.py`, `tests/test_manual_intake.py`, `tests/test_gaps_closed.py`, `tests/test_vacancy_surfacing.py`, `tests/test_hardwall.py` — 158 tests covering scoring, dedup, exclusion, contact merge, Decision 4 hold, and warm-path join.

### Pending

Tested end-to-end with real keys. Slack integration proven live. No remaining blockers.

### Evidence

`banks/intake.py` · `banks/manual_intake.py` · `banks/score.py` · `banks/dedup.py` · `banks/normalise.py` · `banks/exclusion.py` · `banks/csvport.py` · `banks/enrich.py`

---

## MOD-02 — Contact Resolution, Enrichment & Warm-Path Graph

**Status: Build-complete — e2e tested; pending paid Clay/Hunter.io for hands-off contact enrichment**

### What We Built

A 1st-degree contact graph built from Josh's LinkedIn connections, alumni list, and recruiter registry — merged, deduplicated, and queryable for warm-path outreach. Every Tier A/B surfacing attaches known contacts at the company; every cold Tier A/B company is queued for contact enrichment.

### Components

- `banks/warmpath.py` — `find_referral_paths()`: two path types — `direct` (someone at the company) + `recruiter` (vertical-matched recruiter as secondary referral avenue). `describe_contact()` produces a human-readable summary for the Slack card.
- `banks/contact_enrichment.py` — Batch `EnrichmentPort`: `FakeEnrichmentPort` (tests), `ManualCSVEnrichmentPort` ($0 interim — writes `needs_enrichment.csv`, reads `enriched_*.csv` back), `LiveClayEnrichmentPort` (inert until paid plan). `enqueue_company()` skips fresh-cached (30-day TTL) and already-queued rows. `submit_pending()` + `retrieve_and_apply()` drain and write contacts.
- `banks/intake.py` — `ingest_contacts()` merges on LinkedIn URL with `_SOURCE_PRIORITY` (recruiter > alumni > linkedin). `_merge_contact()` upgrades source label and backfills richer fields (Decision 5).

### Locked Decisions

- **Decision 5 (contact merge):** All 64 alumni + 8 recruiters overlap the 1,694 LinkedIn connections. Ingestion merges on LinkedIn URL and upgrades source label rather than skipping — otherwise a warm recruiter is indistinguishable from a generic connection.
- **Decision 6 (Clay manual):** Only 17 of 1,694 LinkedIn contacts have emails. Enrichment is critical, not optional. Manual CSV path is the $0 interim; paid Clay (or Hunter.io) is the production path.

### Validation

`tests/test_contact_enrichment.py`, `tests/test_intake.py` — queue enqueue/dedup/TTL, batch round-trip, unverified flag, ManualCSV roundtrip, merge logic, recruiter referral by vertical. Warm-path join also proven live: Second Nature → Camryn Hare (Director, Client Experience Ops) + 4 other contacts surfaced in a real Slack card.

### Pending

Paid Clay account or Hunter.io for hands-off contact enrichment. All other paths (warm-path join, manual CSV enrichment, contact graph) are live and tested. CLIENT_QUERIES_V2 Clay section.

### Evidence

`banks/warmpath.py` · `banks/contact_enrichment.py` · `banks/contacts.py` · `banks/store/schema.sql` (contacts, enrichment_queue tables)

---

## MOD-03 — Distribution Lanes & Surround Pack

**Status: Build-complete — logic built and tested; pending career-facts.md, Slack app, domain + from-email**

### What We Built

When Josh approves a Tier A opportunity, Banks generates the full applicable surround pack at once — each lane posted as a **separate, individually-approvable Slack card**. Tier B gets a recruiter lane only (lightweight path). Each card carries Approve / Mark sent / Reject / Revise buttons.

**Seven lane types:**

| Lane | Trigger | Send method |
|---|---|---|
| Hiring Manager | Tier A + verified contact email | Email via Relay (on approve) |
| LinkedIn | Tier A + contact without verified email | Copy-paste — "Mark sent" button |
| Warm Intro Ask | 1st-degree contact at company | Internal Slack card |
| Recruiter | Every Tier A/B | Internal Slack card |
| Employee Networking | Other contacts at company (max 2) | Internal Slack card |
| POV Brief | Tier A only | Internal Slack card (review before using) |
| Consulting/Fractional | `pursuit_mode = fractional/consulting` | Internal Slack card |

### Components

- `banks/surround.py` — `generate_surround_pack()`: gates on Tier A for full pack, Tier B for recruiter only; checks company freeze before generating; creates all lane rows atomically, then proposes each to Slack. `advance_warm_intro()` (manual state advance via button); `stall_aged_warm_intros()` (auto-STALL after 7 days); `pending_secondary_escalations()`.
- `banks/lanes.py` — individual drafters: `draft_hiring_manager_lane`, `draft_recruiter_lane`, `draft_employee_lane`, `draft_warm_intro_ask`, `draft_linkedin_lane`, `draft_pov_brief`, `draft_consulting_lane`. All facts-only — LLM is optional (personalisation only, never fact invention).

**No-embellishment enforced:** every drafter raises `ValueError` on empty career-facts. Banks refuses and reports — never invents.

**Warm-intro state machine:** `warm_intros` table; states ASKED → AGREED → INTRODUCED; auto-STALL after 7 days of no movement (manual advance only via Slack buttons; Banks never assumes a human reply). A stalled intro surfaces a single secondary-escalation recommendation — never a blast.

**POV brief:** Tier A only; labelled "draft POV — verify specifics"; built from JD + career-facts + LLM reasoning; no external scraping.

### Locked Decisions

- Tier A → full surround pack; Tier B → recruiter lane only.
- No mutual connection → no warm-intro card.
- No verified email → LinkedIn DM card instead of email card.
- Consulting/fractional `pursuit_mode` → consulting lane auto-added.
- Josh-initiated no-open-role pitches: Josh names company + angle, Banks drafts on request (not auto-generated).

### Validation

`tests/test_surround.py` — surround pack tier gating, career-facts guard, warm-intro state machine (ASKED/AGREED/STALLED), POV brief Tier A only, company freeze skip, multi-card posting, consulting lane routing.

### Pending

`banks/memory/career-facts.md` must be populated with Josh's resume before any real draft content can be generated (currently empty — Banks refuses and flags the gap). CLIENT_QUERIES_V2 item 3. Domain + from-email needed for email lane sending (items 4a–4c). Dedicated Banks Slack app for approval buttons (item 2).

### Evidence

`banks/surround.py` · `banks/lanes.py` · `banks/store/schema.sql` (outreach_lanes, warm_intros tables)

---

## MOD-04 — Follow-up Cadence, Governance & Collision Ledger

**Status: Build-complete — logic built and tested; pending Slack app for Interview/Offer button**

### What We Built

A governance layer that controls the timing, volume, and collision-safety of all outreach — ensuring Banks never over-contacts, never fires after a reply, and never drops a queued touch when the daily cap is hit.

### Components

- `banks/governance.py` — the complete MOD-04 engine:
  - **Daily caps:** `check_and_increment()` — email 40/day, LinkedIn 20/day. Over-cap surfacings overflow to the next day, never dropped.
  - **Collision protection:** `got_reply()` — freezes the company in `company_freeze`, atomically freezes all pending cadence touches at that company, and logs a `replied` funnel event. A 7-day stall surfaces a single secondary-escalation recommendation via `pending_secondary_escalations()`.
  - **Cadence queue:** `queue_cadence()` — creates Day 3 / 7 / 14 touch rows keyed off `outreach_lanes.sent_at` (the real send timestamp — Relay send or manual "Mark sent"). `due_cadence_touches()` returns pending touches due today, filtered to exclude opportunities already `interviewing` or `closed`. `mark_lane_sent()` stamps `sent_at` on a lane row.
  - **14-day contact spacing:** `check_14day_spacing()` queries `outreach_lanes.sent_at` by `contact_id` — no fragile name/email matching.
  - **Network Activation Lite:** `network_activation_due()` — surfaces up to 3 contacts untouched for 14+ days, ranked by seniority (Director/VP/Head/Chief first, then recruiter source, then rest).
  - **Funnel tracking:** `record_funnel_event()`, `record_interview()`, `record_offer()`, `weekly_funnel_summary()`. Funnel events: applied / contacted / replied / intro_made / interview / offer.
- `banks/cadence.py` — `FOLLOW_UP_DAYS = [3, 7, 14]`, `next_follow_up_date()`, `cadence_complete()` (stops on 3 touches or interviewing/closed status).

### New Schema Tables

`outreach_lanes` · `warm_intros` · `cadence_queue` · `governance_ledger` · `company_freeze` · `funnel_events`

### Locked Decisions

- **Day 3 / 7 / 14** cadence (per signed plan; not 5/12/21).
- Cadence keyed off **sent** timestamp — Relay send or manual "Mark sent."
- Cadence auto-stops on: "Got a reply" button, 3 touches reached, or opportunity → interviewing/closed.
- "Got a reply" freezes all other pending outreach at that company atomically.
- 7-day stall → **single** secondary-escalation recommendation — never a blast.
- Governance caps govern Banks' output, not Josh's manual actions.

### Validation

`tests/test_governance.py` — daily cap enforcement, company freeze, `got_reply()` atomicity, cadence queue creation, cadence stop on reply, cadence stop on `interviewing`/`closed` status, 14-day spacing, funnel summary.

### Pending

Dedicated Banks Slack app (item 2) to wire the Interview/Offer button in Slack to `record_interview()` / `record_offer()`. The logic is complete; the Slack trigger is blocked on the app.

### Evidence

`banks/governance.py` · `banks/cadence.py` · `banks/store/schema.sql` (6 new tables)

---

## Cross-Cutting: What Unblocks Everything

The items below are access, inputs, and confirmations — no further engineering is required to act on them.

| Item | Unblocks | Status |
|---|---|---|
| `BANKS_ANTHROPIC_API_KEY` | LLM extraction (JD industry, draft copy) | Awaiting Josh |
| Dedicated Banks Slack app (Socket Mode) | Live approval buttons; Interview/Offer button | Awaiting CTO |
| `career-facts.md` populated with resume | Any real draft content (MOD-03) | Awaiting Josh |
| Domain + from-email + DKIM/SPF | Email lane sending via Relay/Resend | Awaiting Josh |
| Paid Clay account (or Hunter.io) | Hands-off contact enrichment | Awaiting Josh decision |
| Full exclusion list | MOD-06 enforcement | Awaiting Josh |

Full list with details: `CLIENT_QUERIES_V2.md` items 1–11.

---

## Test Suite

| Scope | Tests |
|---|---|
| MOD-01 (intake, scoring, dedup, exclusion) | 158 |
| MOD-02 (contacts, warm-path, enrichment) | included above |
| MOD-03 (surround pack, lanes, warm-intro) | 100 |
| MOD-04 (governance, cadence, funnel) | 100 |
| **Total** | **358 passing** |

Hardwall enforced by `tests/test_hardwall.py` — any FA import or FA credential marker in Banks' environment fails the suite.

---

## Repository

`github.com/jbkcreator/Banks`

- `feature/mod01-mod02-foundation` — MOD-01/02 (PR open, awaiting merge)
- `feature/mod03-mod04-distribution` — MOD-03/04 (build-complete, awaiting review + merge)
- `main` — will reflect both once PRs are merged

_Banks prepares and proposes. Josh decides and approves. Nothing is sent, posted, submitted, or paid without his explicit one-tap approval._
