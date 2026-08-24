# Banks — Detailed Build-Out Plan (post client answers)

Owner: build team · Drafted 2026-08-05 · **Conformance audit added 2026-08-06 (§11–§12)**

**Sources of truth, in precedence order:**

1. `BANKS-CLIENT-ANSWERS.md` — the client's own answers (2026-08-04). **Highest precedence**; where these conflict with the spec, the client wins and the override is recorded in §11.3.
2. `FA-Agent-Lane-Build-Spec-TEAM 2 (2).md` **Part 5 — Banks, Constitution v2.1** — the authoritative feature checklist, mirrored locally at `Banks/banks/constitution.md`.
3. This plan — how we get there.

§11 audits what the constitution requires against what is actually in code, because the task-level reports track *our* build items, not the constitution's line items. Several constitution requirements had no build item at all; §12 creates them.

## 0. Purpose & the one hard constraint

Build **~80% of Banks' code before any real infrastructure exists** — no live domain, no server, no GitHub remote, no production PadSplit login. The only live external we use during this phase is a **Slack test workspace** (free), so the real chat path is exercised for real, not faked.

This is achievable because Banks' value is in its *logic and safety rails*, not its plumbing. The plumbing (domain, mailbox, server, PadSplit login, calendar share, Drive folder) is thin and swappable **if and only if** the code is built behind clean interfaces from day one. That seam is the spine of this plan.

## 1. What the client answers changed (delta from the pre-answer build)

The B01 core and the BANKS-02/03 logic modules were built *before* answers landed, against generic assumptions. The answers overturn several of those assumptions. Reconciling them is the first real work:

| Area                    | Pre-answer assumption (built)                  | Client answer                                                                                                                                                    | Action                                                                                                |
| ----------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Rental system of record | generic room/inquiry tables, Banks-owned       | **PadSplit is system of record** — inventory, vacancy, screening, rent, reviews, comps all live there                                                     | Rework rentals onto a PadSplit**SourcePort**; local DB becomes a cache/mirror, not the truth    |
| Applicant screening     | Banks scores inquiries (income/credit formula) | **No independent screening** — PadSplit screens & presents a pool; Josh approves/declines                                                                 | **Remove** `score_inquiry`; replace with "surface PadSplit-presented applicants for decision" |
| Inquiry replies         | drafted to the tenant                          | inquiries go to**Praise** (property manager) first                                                                                                         | Route drafts to Praise via a contacts layer                                                           |
| Listing platforms       | fixed list                                     | PadSplit primary (self-syndicates) + Roomi + others;**extensible**                                                                                         | Platform-format registry, not hardcoded set                                                           |
| Reviews                 | Google/Zillow                                  | **PadSplit review system**; drop Google entirely; triggers configurable, payment-streak off by default                                                     | Rework review triggers; delete Google path                                                            |
| Turnover                | whole-unit checklist                           | **room-level co-living** — property stays occupied, housemates not party; Praise's real checklist                                                         | Room-level turnover model; account for sitting housemates                                             |
| Rent comps              | Rentometer/Zillow                              | **PadSplit comps**, per-room                                                                                                                               | Comp source = PadSplit SourcePort                                                                     |
| Capital                 | cap rate / cash-on-cash (equity)               | **Roth IRA (not SDIRA)**; short-term secured lending (LTV, capital-stack, borrower experience, exit, term); **HELD pending custodian + legal gates** | **Freeze** module; existing equity math is wrong frame AND blocked — park it                   |
| Email host              | Google Workspace                               | **Cloudflare Email Routing** (free), forward + send-as                                                                                                     | MailPort live adapter targets Cloudflare                                                              |
| Approval                | either style                                   | **two-step** (approve → mark sent); surface **approved-but-unsent queue with age** in morning briefing                                              | Extend dashboard + packets                                                                            |
| Market brief            | weekly                                         | **daily**, degrade gracefully (flag stale if a day missed)                                                                                                 | BriefPort with staleness                                                                              |
| Bills                   | single list                                    | **two categories** (personal vs property-level); property ones roll up per property                                                                        | Add category + property rollup                                                                        |
| Opportunity             | generic roles                                  | **Director/VP PropTech**; resume v14 sole source; flag gaps, never write around them; never submit, show posting first                                     | Tighten to resume-only + gap-flagging                                                                 |
| ROI meter               | figures TBD                                    | **$48/hr**; personal calendar blocks = real conflicts, equal weight                                                                                        | Config value; conflict weighting                                                                      |
| Short-term rental       | not considered                                 | separate STR exists,**out of scope now** but don't preclude later                                                                                          | Property model must not hardcode co-living-only                                                       |

## 2. Architecture — Ports & Adapters (the 80/20 seam)

Banks core knows nothing about PadSplit, Slack, Cloudflare, Google, or a calendar. Every external system is a **Port** (a Python `Protocol`/ABC) with two implementations:

- a **Fake adapter** — deterministic, fixture-driven, built now, drives all tests (the 80%)
- a **Live adapter** — thin, wired when credentials land (the 20%)

```
                 ┌─────────────────────── Banks core ───────────────────────┐
                 │ enforcement · packets · scorecard · scheduler · selfheal   │
                 │ memory · integrity   (all source-agnostic, already built)  │
                 └───────────────▲───────────────────────────▲───────────────┘
   domain modules:               │                           │
   rentals · finance · opportunity · schedule (capital = frozen)
                 │                                            │
        ┌────────┴────────┐                          ┌────────┴────────┐
        │   PORTS (ABCs)   │                          │  every port has  │
        │ SourcePort       │  PadSplit                │  Fake + Live     │
        │ MailPort         │  Cloudflare email        │  adapter         │
        │ ChatPort         │  Slack #banks            │                  │
        │ CalendarPort     │  read-only ICS           │  80% = core +    │
        │ FilePort         │  Google Drive receipts   │  domain + fakes  │
        │ BriefPort        │  daily market brief      │  20% = live      │
        └──────────────────┘                          └──────────────────┘
```

**Six ports:**

1. **SourcePort** (PadSplit) — `list_rooms()`, `vacancies()`, `payment_status()`, `presented_applicants()`, `reviews()`, `room_comps()`. Fake reads CSV/JSON fixtures shaped like PadSplit's real dashboard exports; live adapter is a read-only-login scraper/CSV-puller (chosen form pending — see §6, PadSplit access).
2. **MailPort** (Cloudflare) — `inbound()` (renewals, receipts, forwards), `draft_reply()` (never sends — hands the drafted reply to the ChatPort/outbox). Fake reads `.eml` fixtures.
3. **ChatPort** (Slack) — `post_draft()`, `read_approvals()` (reactions → two-step approve/mark-sent). **Live adapter built now against a test workspace.** Fake = outbox JSON (already built).
4. **CalendarPort** — `events(range)` read-only. Fake reads `.ics` fixtures.
5. **FilePort** (Drive) — `file_receipt(property, attachment)` preserving original. Fake writes to a local dir tree.
6. **BriefPort** — `latest_brief()` with `is_stale()`. Fake reads seeded briefs with timestamps.

**Why this shape wins the 80/20:** the entire behaviour of Banks — every draft it writes, every safety rule, every scorecard line — is exercised end-to-end against fakes. Going live means writing six thin adapters and flipping config. No domain logic changes when infra arrives.

## 3. Current state of the code (keep / rework / freeze)

Repo `FA/Banks/` — 2 commits, 54 tests passing. Triage:

**KEEP as-is (source-agnostic core — already correct):**

- `enforcement.py` — drafts-only egress guard, operator verification. ✅
- `integrity.py` — Immutable Core hash/halt. ✅
- `packets.py` — Decision Packet + Action Queue (answered≠completed already matches two-step Q6). ✅ (extend, don't rewrite)
- `selfheal.py` — retry/dead-letter + temporal freshness. ✅
- `scheduler.py` — cadence skeleton (Eastern). ✅ (adjust: daily brief, morning approved-but-unsent)
- `store/` — SQLite; room-first already. ✅ (extend schema, §5)
- `memory/` — constitution + memory files. ✅

**REWORK (built on wrong assumptions):**

- `rentals.py` — re-seat on SourcePort; delete `score_inquiry`; route to Praise; extensible listing formats; PadSplit reviews; room-level turnover; per-room PadSplit comps.
- `finance.py` — add bill `category` (personal|property) + per-property rollup; receipts preserve original via FilePort.
- `scorecard.py` — add approved-but-unsent-with-age to morning dashboard; market-brief staleness line.
- `opportunity.py` — tighten to resume-v14-only, gap-flagging, PropTech Director/VP framing, never-submit + show-posting-first.
- `schedule.py` — set $48/hr; weight personal calendar blocks as real conflicts.

**FREEZE (blocked by client):**

- `capital.py` — HELD. Existing equity math (cap rate/CoC) is the wrong frame *and* the module is gated on custodian confirmation + legal review. Mark deprecated; build nothing new until both gates clear. When unfrozen, rebuild for short-term secured lending (LTV, capital-stack position, borrower experience, exit, term).

## 4. Build phases

### Phase A — Ports & seam (foundation for everything) — **STATUS: BUILT 2026-08-05** (ChatPort, MailPort, CalendarPort, FilePort, LLMPort + fakes + `container.py` DI; SourcePort/PadSplit fake only, live adapter awaits creds)

Define the six Port ABCs + a `Fakes` package driving all of them from fixtures. Wire dependency-injection so domain modules take ports as constructor args (no global singletons). This is the enabling move for the whole 80%.

- Deliverable: `banks/ports/` (interfaces) + `banks/adapters/fake/` + fixture set in `tests/fixtures/`.
- **Not yet built.** Design lives in §2. A first build attempt on 2026-08-05 was reverted to keep this a planning session; no ports/adapters code exists in the repo. Build begins only on explicit go-ahead.

### Phase B — Reconcile core to answers

- Extend `packets.py` for the approved-but-unsent aging surface.
- `scorecard.py`: morning dashboard = yesterday recap · today's 1–3 · **approved-but-unsent w/ age** · rooms/vacancy · money-due 7-day · schedule+prep · one learning item · scorecard line · market-brief-staleness flag.
- `scheduler.py`: daily brief ingest slot; morning briefing 7:30 ET.
- BriefPort + staleness.

### Phase C — Rentals on PadSplit (the biggest rework, BANKS-02)

Re-seat every rental workflow on SourcePort + the Praise contacts layer:

- Vacancy: pull from SourcePort, same-day detection, days-vacant clock (revenue lever #1).
- Listings: extensible platform-format registry (PadSplit primary + Roomi + others from Praise); drafts only.
- Inquiries: draft replies **to Praise**, pointing to PadSplit application flow.
- Applicants: surface **PadSplit-presented** pool for Josh's approve/decline — no independent scoring.
- Rent status: read from SourcePort; late-payer nudge drafts (never touches funds).
- Maintenance: route to Praise; vendor drafts from Praise's list; track to closure; >7d scorecard line.
- Reviews: PadSplit review system; triggers (maintenance-resolved, smooth move-in, unprompted thanks); payment-streak configurable + off.
- Turnover: Praise's room-level checklist; housemates-not-party awareness.
- Rate optimizer: per-room PadSplit comps → raise/hold memo w/ $ impact.

### Phase D — Finance & schedule (BANKS-03, minus capital)

- Bills: two-category tagging + per-property rollup; 7-day/1-day nudges; never pays.
- Subscriptions: keep/kill memos (already built; keep).
- Receipts: MailPort inbound → FilePort per-property filing, **original attachment preserved**.
- Opportunity: resume-v14-only drafter; gap-flagging; PropTech Director/VP; never submit, show posting first; follow-up ledger; interview briefs.
- Schedule: read-only calendar conflicts (personal blocks = real conflicts); occasions; ROI meter @ $48/hr.

### Phase E — Slack live path (uses test workspace — part of the 80%)

- ChatPort live adapter against a **test Slack workspace**: post drafts, read reactions for two-step approval (✅ approve → then "sent" mark). Prove the real interaction, not just outbox. Swap to the client's real workspace token later = config change only.

### Phase F — Integration, acceptance, hardening

- End-to-end "day in the life" runs against all fakes: seeded vacancy→Praise draft; seeded late payment→nudge; seeded PadSplit-presented applicant→surfaced; seeded bill→nudge; seeded calendar conflict→flag; seeded resume+posting→application draft with a gap flagged.
- Re-run and extend the **FA hard-wall harness** (must stay green through all rework — no FA import/env/query path ever creeps in).
- Target: full suite green; ~80% of total planned code complete.

### Phase G — Infra-dependent 20% (deferred, not this phase)

Only when creds land: live MailPort (Cloudflare), live SourcePort (PadSplit login), real Slack token swap, calendar share, Drive folder, Hetzner deploy, GitHub push. See §6.

### Phase H — Capital (separately gated, not infra)

Frozen until custodian + legal gates clear. Then rebuild for short-term secured lending underwriting.

## 5. Data model changes (`store/schema.sql`)

- Add `properties` table (first-class parent) — rooms FK to it; enables per-property rollups and keeps the door open for the out-of-scope STR (a property with a different `kind`).
- `rooms`: keep room-first; ensure per-room rate (already present); add `padsplit_room_id` for sync mapping.
- Replace `inquiries.score` usage — applicants now come from PadSplit presented pool; add `applicants` table (padsplit_applicant_id, room_id, status presented|approved|declined).
- web `bills`: add `category` (personal|property) + keep `property_address`/FK for rollup.
- `reviews`: add table (room_id, trigger, drafted_at) tied to PadSplit review system.
- `market_brief`: add table (received_at, body) for BriefPort staleness.
- Keep additive/idempotent (`init_db` pattern; no destructive migrations — matches ADR-0024 scripts-only discipline in the parent project, applied here as fresh-schema evolution since Banks has no prod DB yet).

## 6. The infra-dependent 20% (what actually needs creds)

| Item                                            | Blocked on              | Owed by                     | Our action today                                                                                                                                                                        |
| ----------------------------------------------- | ----------------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PadSplit access**form decision**          | us                      | **us — due today**   | Answer:**read-only login** now (no public API exists; CSV export is the fallback). Build live adapter as a read-only-login puller; validate against live data when login arrives. |
| Cloudflare Email Routing feasibility            | us                      | us                          | Confirm forward + send-as covers inbound read + drafted replies before client spends on paid mail.                                                                                      |
| Monthly operating cost figure (Q24)             | us                      | **us — owed**        | Send conservative estimate (Cloudflare free + Slack free + GitHub free + Hetzner ~$5–10/mo + LLM usage) so ROI is real from first run; true up after 2–3 wks.                         |
| Domain + registrar                              | client                  | client (today)              | Wire mailbox once received → announce "mailbox live" (triggers Q19 bills list + Q23 calendar share).                                                                                   |
| Slack real token                                | client                  | client (today)              | Config swap from test workspace.                                                                                                                                                        |
| Praise contacts + listing/vendor/turnover lists | client (via Praise)     | client (today)              | Populate contacts layer + platform registry + vendor table + turnover checklist.                                                                                                        |
| Google Drive receipts folder                    | client                  | client                      | FilePort live target.                                                                                                                                                                   |
| Calendar share                                  | client                  | client (after mailbox live) | CalendarPort live target.                                                                                                                                                               |
| Hetzner VM + DB + deploy                        | us                      | us (Hari-approved)          | Provision after code stabilises.                                                                                                                                                        |
| GitHub repo → heu.solution                     | client                  | client                      | `git remote add` + push when created.                                                                                                                                                 |
| Capital module                                  | custodian + legal gates | client                      | Do not start.                                                                                                                                                                           |

## 7. Sequencing & dependencies

```
A (ports+fakes) ──► B (core reconcile) ──► C (rentals) ─┐
                                     └─► D (finance/sched)├─► F (integration+acceptance)
E (slack live, test wkspace) ───────────────────────────┘
G (infra 20%) ── after F, as creds land
H (capital) ── after client gates clear (independent)
```

A is the gate for everything (ports must exist before modules can be re-seated). C and D run in parallel after B. E runs in parallel throughout (only needs the test workspace). Capital stays out.

## 8. Testing strategy

- **Fakes drive everything** — deterministic fixtures per port; no network in the default suite.
- **Hard-wall harness is the invariant** — runs on every change; no FA import/env/query path may appear (static AST + seeded-probe, already built).
- **Drafts-only invariant** — every domain action asserts it produces a `Draft`, never an egress; capital's professional-review flag and never-submit for opportunities stay structurally enforced.
- **Acceptance fixtures** — one per standing job, seeded, proving same-day/same-hour bars.
- **Slack live smoke** — a thin, opt-in test against the test workspace (marked, not in the default CI run) proving post + reaction-read.
- Target coverage: all domain logic + all fakes + ports; live adapters get smoke tests only until real creds validate them.

## 9. Risks & watch-items

- **PadSplit has no public API** (confirmed by research) — the live SourcePort is a read-only-login puller/CSV consumer, inherently more fragile than an API and subject to PadSplit's ToS. Mitigate: isolate all fragility in the live adapter; keep the fixture contract stable; CSV-export path as fallback; flag to client that same-day detection via login-pull is best-effort. **Open research item still out for confirmation.**
- **Client-answer authenticity** — several consequential items (Roth IRA gates, Praise as a real person, 414 commercial terms) are taken from pasted text, unverified from our side. Capital stays frozen regardless; treat Praise/contacts as data to confirm on first real hand-off.
- **Scope creep from STR (Q18a)** — explicitly out of scope; the only obligation is the `properties`-parent model so adding it later isn't a rewrite.
- **Two-step approval drift** — the real failure mode the client named is "approved but never sent." The approved-but-unsent-with-age surface is a first-class dashboard requirement, not a nice-to-have.

## 10. Definition of done for "80% before infra"

- Six ports defined; all six fakes built; DI wired.
- Core reconciled to answers (packets/scorecard/scheduler/brief).
- Rentals, finance, opportunity, schedule reworked and green against fakes.
- Slack live path proven on the test workspace.
- Full acceptance suite + hard-wall harness green.
- Capital frozen; infra-live adapters stubbed behind ports awaiting creds.
- Remaining 20% = writing six thin live adapters + provisioning + capital (gated) — no domain logic left to write.

> **Superseded 2026-08-06.** The last bullet was wrong: §11 found constitution line items with **no domain logic written at all**. The 80% figure measured our own task list, not the constitution. Revised definition of done is in §12.4.

---

## 11. Constitution conformance audit — 2026-08-06

### 11.1 Why this section exists

Progress so far was tracked against *our* build items (ports, pipelines, the eleven-item list). Those are all green. But the authoritative feature list is **Part 5 of the Team 2 spec** (the Banks Constitution v2.1), and it was never walked line-by-line against the code. Doing that walk found requirements with **no build item, no code, and therefore no test** — invisible to a suite that is 128-for-128 green, because nothing was ever asked of them.

Method: every clause of `banks/constitution.md` (standing jobs 1–10, weekly scorecard, mechanics block, output rules, ramp-up, hard rules) checked against the package by grep and file read. Verified, not assumed.

### 11.2 Gap register

**A — Missing standing-job capability (functional holes, not polish)**

| #  | Requirement                                             | Ref   | State                       | What is actually missing                                                                                                                                                                                                                                                                                       |
| -- | ------------------------------------------------------- | ----- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A1 | **Collections watched weekly, nudges drafted day 1**    | Job 3 | ✅ **Built 2026-08-06**      | `banks/collections.py` + `rent_charges`/`rent_payments`. `collections_on_time_pct` now writable. Day-1 nudge idempotent. |
| A2 | **Morning dashboard: 7 named elements**                 | Job 1 | ✅ **Built 2026-08-06**      | 12-section brief: yesterday recap, schedule, Daily Find, scorecard line, Collections, Deadline radar all added. Constitution-ordered. |
| A3 | **Deadline radar**                                      | Job 7 | ✅ **Built 2026-08-06**      | `_deadline_radar_lines()` in `briefing.py`. Sweeps decision deadlines, promises, lease ends, bills. |

**B — Scorecard & governance**

| #  | Requirement                          | Ref       | State             | What is actually missing                                                                                                                                                                                                     |
| -- | ------------------------------------ | --------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B1 | Scorecard "Plus:" block              | Scorecard | ✅ **Built 2026-08-06** | `render_plus_block()` in `scorecard.py`. |
| B2 | **3 reds → Issue**                   | Scorecard | ✅ **Built 2026-08-06** | `banks/issues.py` + `issues` table. `maybe_open_issue_for_week`, `maybe_open_streak_issue`. |
| B3 | **Weekly biggest-miss nomination**   | Mechanics | ✅ **Built 2026-08-06** | `record_miss()` + `missing_miss_weeks()`. Absence flagged as reporting failure. |

**C — Learning & memory mechanics**

| #  | Requirement                                                             | Ref        | State                | Notes |
| -- | ----------------------------------------------------------------------- | ---------- | -------------------- | ----- |
| C1 | Nightly reflection proposes **one-line amendment diffs**, never self-installed | Mechanics  | ✅ **Built 2026-08-06** | `propose_amendment()` in `reflection.py`. AMENDABLE_SECTIONS tuple; never self-installs. |
| C2 | **Lesson quarantine** (LOCAL/PROVISIONAL until 2+ instances)             | Mechanics  | ✅ **Built 2026-08-06** | `banks/lessons.py` + `lessons` table. Auto-promotes at 2 instances; FLEET = manual only. |
| C3 | **Correction taxonomy (8 codes)**                                        | Mechanics  | ✅ **Built 2026-08-06** | `CORRECTION_CODES` in `approval.py`. `record_correction()` → `corrections` table. |
| C4 | Immutable-core hashing                                                  | Hard rules | ✅ **Armed 2026-08-06** | `constitution.hash` generated. `Container.live()` calls `verify()` at startup. |

**D — Contact discipline**

| #  | Requirement                                                    | Ref       | State         | Notes |
| -- | -------------------------------------------------------------- | --------- | ------------- | ----- |
| D1 | **One personal suppression list, permanent**, checked before every draft | Mechanics | ✅ **Built 2026-08-06** | `suppression_list` table. `check_contact_discipline()` in `contacts.py` wired into `flow.propose()`. |
| D2 | **Touch log + 48h collision flag**                             | Mechanics | ✅ **Built 2026-08-06** | `touch_log` table. 48h window enforced in `flow.propose()`. |
| D3 | FA-name overlaps flagged to Josh                               | Mechanics | ✅ **Built 2026-08-06** | `banks/overlap.py`. Loads `BANKS_FA_NAME_LIST_PATH`; surfaces INTERNAL flag draft. |

**E — Compute discipline**

| #  | Requirement                                                            | Ref                     | State         | Notes |
| -- | ---------------------------------------------------------------------- | ----------------------- | ------------- | ----- |
| E1 | **Cheap tier** for triage/hygiene, **premium** for anything Josh reads | Mechanics               | ✅ **Built 2026-08-06** | `CHEAP_MODEL`/`PREMIUM_MODEL` in `llmport.py`. |
| E2 | **Daily cap with auto-cutoff**                                         | Mechanics + Part 1 §1.5 | ✅ **Built 2026-08-06** | `check_daily_cap()` in `compute.py`. `DailyCap` raised on breach. |
| E3 | **Weekly cost on the scorecard**                                       | Mechanics               | ✅ **Built 2026-08-06** | `log_llm_call()` → `activity_log`. `weekly_compute_cost_cents()` for scorecard. |

**F — Operational safety & behaviour**

| #  | Requirement                                                                 | Ref                | State         | Notes |
| -- | --------------------------------------------------------------------------- | ------------------ | ------------- | ----- |
| F1 | **Kill command** ("STOP ALL" / "STOP Banks") halts within one cycle         | Part 1 §1.1(11)    | ✅ **Built 2026-08-06** | `banks/halt.py`. `run_job()` calls `check_halt()`. Socket listener detects and posts. |
| F2 | Watchdog auto-restart + fuel gauge                                          | Part 1 §1.8        | ⏸ **Phase G** | Process-supervision — needs the server. Recorded so it isn't lost. |
| F3 | **Ramp-up:** first 30 days ask freely, then batch                           | Ramp-up            | ✅ **Built 2026-08-06** | `banks/rampup.py`. `should_batch_to_brief()` routes after window. |
| F4 | **Sign "— Banks."** on every output                                         | Output rules       | ✅ **Built 2026-08-06** | `enforcement.sign()`. Idempotent. |

### 11.3 Deliberate spec overrides (client answer wins — do not "fix" these back)

Two places where the constitution and the client's answers disagree. The client is higher precedence, so the code follows the client; both are recorded here so a future reader doesn't mistake them for bugs.

| Constitution says                                        | Client says                                                                                          | Code does                                                                                                                                              | Why                                                                                                                                                                                                     |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Job 2: "inquiries **pre-scored**"                        | Q13: **no independent screening** — PadSplit screens and presents a pool; Josh approves/declines | Independent scoring removed (`score_inquiry`, `ApplicantCriteria`, `InquiryFacts` deleted); `surface_presented_applicant()` relays PadSplit's own presentation | Client answer is later (2026-08-04) and explicit. A fair-housing-adjacent scoring formula we invented is exactly what he ruled out. A hard-wall-style test asserts the scorer stays gone. |
| FA Intelligence Bridge: **weekly** Market Intelligence Brief | Q7: **daily**, pasted into `#banks`, flag stale if a day missed                                   | `briefport.py` with a 1-day freshness window                                                                                                           | Client set the cadence explicitly and named the degradation behaviour.                                                                                                                                  |

### 11.4 Verdict

The build is sound where it was aimed: ports, relay isolation, the approval loop, and the four forwarded-email pipelines are real and tested. But **conformance to the constitution is roughly 70%, not the ~95% the task reports imply** — because the reports measured our task list. Nineteen requirements above have no code. Four of them (A1 collections, D1/D2 contact discipline, E2 daily cap) are the kind of gap a client notices, and two (C4 inert tamper rail, F1 kill command) are safety rails that read as built but are not armed.

None of it is hard. It is mostly small, well-specified mechanisms that were never on a task list.

---

## 12. Phase I — Constitution conformance

> **Status: COMPLETE — 2026-08-06.** All 17 buildable items built and tested. Item 18 deferred to Phase G (needs server). Suite: **220 tests passing**.

### 12.1 Tier 1 — visible functional holes ✅

1. ✅ **Collections module** — `banks/collections.py` + `rent_charges`/`rent_payments` tables. `record_charge`, `record_payment`, `overdue_charges`, `collections_on_time_pct` (scorecard line 5 now writable), `surface_overdue_nudges` (day-1 idempotent nudge to Praise). 8 tests in `test_tier1.py`.
2. ✅ **Complete the morning brief** — 12 sections now: Collections, Deadline radar, Yesterday recap, Today's schedule, Daily Find, Today's scorecard line added. Section order is constitution-ordered. `test_section_order_is_fixed` pinned.
3. ✅ **Daily Find** — `banks/find.py`. `record_find`, `get_find`, `find_brief_lines`. Honest "none" when nothing surfaced. 6 tests.
4. ✅ **Deadline radar** — `_deadline_radar_lines()` in `briefing.py`. Sweeps decision deadlines, promise due dates, lease ends, bill dates (7/30-day window). 4 tests.
5. ✅ **Sign "— Banks."** — `enforcement.sign()`. Idempotent. 3 tests pin the invariant.

### 12.2 Tier 2 — governance & discipline ✅

6. ✅ **Scorecard "Plus:" block** — `render_plus_block()` in `scorecard.py`. Apps queued/submitted, maintenance >7d, corrections, misses owned, today's find.
7. ✅ **Issues** — `banks/issues.py` + `issues` table. `open_issue`, `close_issue` (artifact required), `maybe_open_issue_for_week` (3-reds), `maybe_open_streak_issue` (3 consecutive). 5 tests.
8. ✅ **Contact discipline** — `banks/contacts.py` + `suppression_list`/`touch_log` tables. `check_contact_discipline()` wired into `flow.propose()` — every outbound draft hits it. Suppression + 48h collision. 9 tests.
9. ✅ **Correction taxonomy** — 8 codes (`CORRECTION_CODES`) in `approval.py`. `record_correction()` → `corrections` table. 2 tests.
10. ✅ **Lesson quarantine** — `banks/lessons.py` + `lessons` table. LOCAL → PROVISIONAL (auto at 2 instances) → FLEET (manual only). 4 tests.
11. ✅ **Amendment proposals** — `propose_amendment()` in `reflection.py`. `AMENDABLE_SECTIONS` tuple; non-amendable sections raise. Never self-installs. 3 tests.
12. ✅ **Weekly biggest-miss** — `record_miss()` + `missing_miss_weeks()` in `scorecard.py` + `weekly_misses` table. Absence flagged as reporting failure. 3 tests.

### 12.3 Tier 3 — safety rails & compute ✅

13. ✅ **Integrity check armed** — `banks/constitution.hash` generated. `Container.live()` calls `integrity.verify()` at startup. `test_real_constitution_passes_verify` pins the shipped hash. 5 tests.
14. ✅ **Kill command** — `banks/halt.py`. In-process halt flag, `set_halt`/`check_halt`/`clear_halt`. `is_halt_command()` for "STOP ALL"/"STOP Banks". `run_job()` calls `check_halt()` at entry. Socket listener detects and posts confirmation. 6 tests.
15. ✅ **Compute discipline** — `banks/compute.py` + `llmport.py` tiers. `CHEAP_MODEL` (haiku) / `PREMIUM_MODEL` (sonnet-5). Per-call cost logging to `activity_log`. `check_daily_cap()` raises `DailyCap`. `weekly_compute_cost_cents()` for scorecard. 6 tests.
16. ✅ **Ramp-up mode** — `banks/rampup.py`. 30-day window from `BANKS_RAMPUP_START`. `should_batch_to_brief()` routes non-urgent items after ramp-up. 5 tests.
17. ✅ **FA-name overlap flagging** — `banks/overlap.py`. Loads `BANKS_FA_NAME_LIST_PATH`. `check_and_flag()` surfaces INTERNAL draft and blocks. Hard wall: never contacts, never coordinates. 5 tests.
18. ⏸ **Watchdog + fuel gauge** — deferred to **Phase G** (needs the server).

### 12.4 Definition of done — met

- All 17 buildable §11.2 gaps built and tested. Item 18 explicitly deferred (server-gated).
- `test_real_constitution_passes_verify` ensures the constitution and hash stay in sync.
- §11.3 overrides preserved: applicant scorer deleted, brief cadence daily.
- **Remaining work is plumbing only**: six thin live adapters + provisioning + capital (gated). No domain logic left to write.
