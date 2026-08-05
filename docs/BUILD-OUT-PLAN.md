# Banks — Detailed Build-Out Plan (post client answers)

Owner: build team · Drafted 2026-08-05 · Source of truth for answers: `BANKS-CLIENT-ANSWERS.md` · Constitution: `Banks/banks/constitution.md` (Part 5 v2.1)

## 0. Purpose & the one hard constraint

Build **~80% of Banks' code before any real infrastructure exists** — no live domain, no server, no GitHub remote, no production PadSplit login. The only live external we use during this phase is a **Slack test workspace** (free), so the real chat path is exercised for real, not faked.

This is achievable because Banks' value is in its *logic and safety rails*, not its plumbing. The plumbing (domain, mailbox, server, PadSplit login, calendar share, Drive folder) is thin and swappable **if and only if** the code is built behind clean interfaces from day one. That seam is the spine of this plan.

## 1. What the client answers changed (delta from the pre-answer build)

The B01 core and the BANKS-02/03 logic modules were built *before* answers landed, against generic assumptions. The answers overturn several of those assumptions. Reconciling them is the first real work:

| Area | Pre-answer assumption (built) | Client answer | Action |
| ---- | ----------------------------- | ------------- | ------ |
| Rental system of record | generic room/inquiry tables, Banks-owned | **PadSplit is system of record** — inventory, vacancy, screening, rent, reviews, comps all live there | Rework rentals onto a PadSplit **SourcePort**; local DB becomes a cache/mirror, not the truth |
| Applicant screening | Banks scores inquiries (income/credit formula) | **No independent screening** — PadSplit screens & presents a pool; Josh approves/declines | **Remove** `score_inquiry`; replace with "surface PadSplit-presented applicants for decision" |
| Inquiry replies | drafted to the tenant | inquiries go to **Praise** (property manager) first | Route drafts to Praise via a contacts layer |
| Listing platforms | fixed list | PadSplit primary (self-syndicates) + Roomi + others; **extensible** | Platform-format registry, not hardcoded set |
| Reviews | Google/Zillow | **PadSplit review system**; drop Google entirely; triggers configurable, payment-streak off by default | Rework review triggers; delete Google path |
| Turnover | whole-unit checklist | **room-level co-living** — property stays occupied, housemates not party; Praise's real checklist | Room-level turnover model; account for sitting housemates |
| Rent comps | Rentometer/Zillow | **PadSplit comps**, per-room | Comp source = PadSplit SourcePort |
| Capital | cap rate / cash-on-cash (equity) | **Roth IRA (not SDIRA)**; short-term secured lending (LTV, capital-stack, borrower experience, exit, term); **HELD pending custodian + legal gates** | **Freeze** module; existing equity math is wrong frame AND blocked — park it |
| Email host | Google Workspace | **Cloudflare Email Routing** (free), forward + send-as | MailPort live adapter targets Cloudflare |
| Approval | either style | **two-step** (approve → mark sent); surface **approved-but-unsent queue with age** in morning briefing | Extend dashboard + packets |
| Market brief | weekly | **daily**, degrade gracefully (flag stale if a day missed) | BriefPort with staleness |
| Bills | single list | **two categories** (personal vs property-level); property ones roll up per property | Add category + property rollup |
| Opportunity | generic roles | **Director/VP PropTech**; resume v14 sole source; flag gaps, never write around them; never submit, show posting first | Tighten to resume-only + gap-flagging |
| ROI meter | figures TBD | **$48/hr**; personal calendar blocks = real conflicts, equal weight | Config value; conflict weighting |
| Short-term rental | not considered | separate STR exists, **out of scope now** but don't preclude later | Property model must not hardcode co-living-only |

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

**Plus RELAY (deterministic executor, not a port, not the agent):** the one component that *sends*. When Josh taps ✅ approve, Relay replays the exact approved draft byte-for-byte (no LLM, no interpretation), writes a receipt, and carries an idempotency key. The agent holds no send capability; Relay is the only outbound-send path, host-allowlisted, in `adapters/live/` (or its own `relay/` package). See A-D9. This is how "approve → auto-send, no manual step" is achieved without breaking the agent's drafts-only core (mirrors the FA Cora→Relay resolution).

**Why this shape wins the 80/20:** the entire behaviour of Banks — every draft it writes, every safety rule, every scorecard line — is exercised end-to-end against fakes. Going live means writing the thin adapters + Relay and flipping config. No domain logic changes when infra arrives.

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

### Phase A — Ports & seam (foundation for everything) — **STATUS: PLANNING (not started)**
Define the six Port ABCs + a `Fakes` package driving all of them from fixtures. Wire dependency-injection so domain modules take ports as constructor args (no global singletons). This is the enabling move for the whole 80%.
- Deliverable: `banks/ports/` (interfaces) + `banks/adapters/fake/` + fixture set in `tests/fixtures/`.
- **Not yet built.** Design lives in §2. A first build attempt on 2026-08-05 was reverted to keep this a planning session; no ports/adapters code exists in the repo. Build begins only on explicit go-ahead.

**Phase A design decisions (grilled 2026-08-05):**
- **A-D1 — Local store role:** SQLite is a **read-through cache/mirror** of PadSplit; **PadSplit wins on any conflict** and Banks re-syncs. Banks-native data (bills, promises, decision packets, scorecard history) stays authoritative in SQLite — PadSplit knows nothing of it. A staleness marker flags a mirror that failed to refresh (same discipline as the daily market brief) rather than silently trusting old data.
- **A-D2 — Store engine:** **SQLite confirmed** — correct for a single-user, single-process, single-server, hard-walled agent (not a compromise). Enable **WAL mode + busy-timeout** so a sync job and a dashboard render can't collide. Postgres is over-engineering here; if Banks were ever cloned into a multi-tenant product, the store-seam makes the swap a contained change (domain logic untouched) — but that would be a new effort, not this build.
- **A-D3 — Fake data seeding:** **hybrid, weighted to in-memory.** Fakes take data in their constructor (self-contained behaviour tests = the 80%). A small set of **on-disk fixtures captures the real format** of each external (PadSplit export sample, a real `.eml`, a real `.ics`) purely so the Live adapter's parser has a real contract to build against — not to drive behaviour tests.
- **A-D4 — PadSplit has no public API (confirmed by research: no API, no Zapier, no published tooling; only PadSplit-side partner hooks — RemoteLock, Furnished Finder).** The Live SourcePort is therefore scraping-class work, quarantined behind the port. All fragility lives in the Live adapter; the fixture/format contract (A-D3) keeps it honest.
- **A-D5 — Ingestion is AUTOMATED direct-from-PadSplit (client requirement, locked).** The client explicitly rejected a human intermediary ("source directly from PadSplit... pull vacancy rather than waiting on a notification from me or Praise"). So Banks must pull automatically. CSV-export drop + a `#banks` manual line are demoted to **degraded fallback only** (used when an automated pull fails; Banks flags stale rather than silently trusting old data) — not the primary path.
  - **Mechanism (OPEN — blocked on recon):** private-JSON-API replay (preferred: log in, hold session, call the dashboard's own internal JSON endpoints, parse JSON not HTML) vs. headless-browser automation (Playwright fallback). Cannot be decided blind.
  - **Blocking research ticket — "Recon PadSplit host dashboard":** log into a real host account, inspect network/auth traffic, determine the pull mechanism, capture JSON fixtures (feeds A-D3), and surface the reality on **2FA, bot-protection (Cloudflare/PerimeterX-class — we hit this with HOVER on FA), and session handling.** SourcePort's *live* contract can't be finalized until this resolves. The **Fake** SourcePort + all domain logic are unblocked and build now against the data *shape*.
  - **Client sign-off required (risk):** automated access likely violates PadSplit ToS and risks *his* income-bearing account. This needs his explicit, informed acceptance — not a silent build decision. Flag before any live automation runs.
- **A-D6 — Hard wall redefined for an integration-heavy agent (reworks the B3 harness).** The wall means "no Forced Action, ever" — **not** "no network." Banks legitimately reaches PadSplit, Slack, Cloudflare, Google (none are FA). The current harness test that bans all HTTP imports is superseded by a two-layer rule:
  - **Structural (layer b):** core + domain modules stay **network-free** — the AST test still forbids `requests`/`httpx`/`urllib`/browser-automation imports *in `banks/` domain code*. Egress is confined to `banks/adapters/live/`, the only package permitted those imports.
  - **Semantic (layer a):** even inside live adapters, a fixed **host allowlist** (padsplit.com, slack.com, Cloudflare, googleapis.com — the only externals Banks may reach) is enforced; any FA-shaped hostname/endpoint/credential, or any host off the allowlist, fails the harness.
  - Net: *core physically cannot touch the network; adapters can, but only to a small allowlist of non-FA services.* Stronger and truer than "no HTTP anywhere." The existing no-FA-import / no-FA-env-leak / no-FA-query-name assertions all stay.
- **A-D7 — Dependency injection: per-module ports (b) + a `Ports` assembly root (a).** Each domain module takes only the specific ports it needs in its constructor (`RentalOps(source, chat)`, `FinanceOps(mail, file, chat)`) — clearest dependencies, cleanest tests, and a module can't secretly reach a port it wasn't given (finance is handed `chat` but no PadSplit write path, so its dependency list *documents* that it can't touch PadSplit). A single `banks/container.py` `Ports` container is the assembly root: builds all-fakes or all-live and hands each module its slice. No global singletons (order-dependent tests, hidden coupling — exactly what the wall wants visible).
- **A-D8 — Two-step approval loop (client's #1 named failure mode: "approve then never send").**
  - **Correlation by `draft_ref`:** `ChatPort.post_draft()` returns a `draft_ref` (Slack message ts/id); Banks stores it on the packet row; `read_approvals()` returns `(draft_ref, action)` matched by ref. Survives restarts, no ambiguity. (Not position/recency matching.)
  - **Four-reaction vocabulary:** ✅ approve ("good, I'll send it" → enters approved-but-unsent aging queue) · 📤 sent ("I actually did it" → complete, leaves queue) · ❌ reject · ✍️ edit (revise). The ✅→📤 gap *is* the aging queue surfaced with age in the morning briefing.
  - **Skip-to-sent allowed:** 📤 is valid without a prior ✅ — Josh may act fast; forcing two taps would create the friction that stops people marking things.
  - **Reuses existing structure:** maps directly onto `packets.py` — `answered_at` = ✅ approve, `completed_at` = 📤 sent (two timestamps already built and proven distinct). ChatPort only needs to return `draft_ref` and emit these four actions; the aging queue is `answered_at set, completed_at null`.
- **A-D9 — Auto-send on approval via the RELAY pattern (NOT agent-held SMTP).** Requirement: when Josh taps ✅ approve, the email/message goes out automatically — no manual copy-paste. Chosen mechanism mirrors the FA project's Cora→Relay resolution of this exact "drafts-only vs. actually send" contradiction:
  - **The Banks agent never sends.** No SMTP in the agent, by construction; the drafts-only harness assertion stays (no send client importable in agent/domain code).
  - **A separate deterministic executor (`relay`)** — no LLM, no interpretation — watches for the ✅ approve signal and sends **exactly** the approved draft, byte-for-byte, then writes back a **receipt** (sent timestamp, recipient, channel). "Approved" ≠ complete; **receipted** = complete (the 📤 state can be set by the receipt, not only a manual tap).
  - **Idempotency key per send** so a retry cannot double-send.
  - **Constitutional note:** this satisfies Part 5's "everything through Josh's tap" — the agent drafts, a dumb executor replays only what Josh approved. It does change the operating model from "Josh sends manually" to "approval auto-sends," so the constitution's drafts-only wording is annotated: *the agent* still sends nothing; execution moves to Relay, exactly as Cora's immutable core stayed intact while Relay carried execution. **Flag for client confirmation** that auto-send-on-approve (vs. manual send) is intended.
  - **Hard-wall impact:** `relay` is the *only* component with an outbound send path; it lives in `adapters/live/` (or its own `relay/` package), host-allowlisted, and is exercised in Phase A/E against fakes + the Slack test workspace. The agent's no-send guarantee is unchanged.
  - **Supersedes A-D... MailPort inbound-only stance:** MailPort stays inbound-only for *reading*; outbound is Relay's job, not MailPort's, not the agent's.

### Phase B — Reconcile core to answers
- Extend `packets.py` for the approved-but-unsent aging surface.
- `scorecard.py`: morning dashboard (order per B-D1 below).
- `scheduler.py`: daily brief ingest slot; morning briefing 7:30 ET.
- BriefPort + staleness.

**Phase B design decisions (grilled 2026-08-05):**
- **B-D1 — Morning briefing is failure-mode-first.** Section order for the 60-second scan:
  1. **Approved-but-unsent queue, with age** (client's #1 named failure mode — leads the scan)
  2. Today's 1–3 pre-ranked actions
  3. Rooms/vacancy status + days-vacant clock (revenue lever #1)
  4. Money due, 7-day window
  5. Schedule + prep (today's calendar + conflicts)
  6. Yesterday recap
  7. One learning item
  8. Scorecard line
  9. Market-brief staleness flag (only if the daily brief is missing/stale)
- **B-D2 — Market brief: blob + labelled-when-stale.**
  - **Consumption = text blob** (not structured parse). Banks stores the latest brief verbatim with a timestamp; injects it into the LLM context when drafting anything market-relevant (re-listing, rate memo). The brief is qualitative market colour to inform drafts, not a queryable data feed — parsing prose into fields is brittle and buys nothing the LLM can't do by reading the blob. Matches the document-only hard-wall reality (Josh forwards it; it's never an FA data pull).
  - **Staleness = labelled, not dropped.** Brief is a daily input → **stale after ~36h**. When stale, Banks still uses the last brief but **stamps every market-derived line "market read as of [date], N days old"** and raises the staleness flag on the morning briefing (B-D1 #9). The client's objection was *silent* staleness, not use of older context — an honestly-labelled day-old read beats no market context.
  - **Hard escalation:** if the brief goes **>5 days stale**, escalate to *drop* — Banks stops citing market context entirely and says so, rather than leaning on a badly outdated read.
- **B-D3 — `#banks` message classification: LLM classify + confirm-on-ambiguity (+ optional prefixes).** `#banks` is multiplexed (daily market brief · vacancy fast-path line · instruction/question · noise · Banks' own drafts · reaction approvals). Banks classifies each inbound human message into `{market_brief, vacancy_signal, instruction, question, noise}` and routes it:
  - Confident → act + acknowledge ("Got today's brief ✅" / "Logged Room 3 vacant, drafting re-listing").
  - Unsure → *ask one question and stop* (matches the constitution's "unsure = ask one question"); high-stakes misroutes (missed vacancy, mis-ingested instruction) are avoided rather than risked.
  - Optional command prefixes (`BRIEF:`, `VACANT:`, `ASK:`) work as a deterministic fast-path but are never required.
  - **Prompt-injection safety (load-bearing):** a pasted market brief is *context, never a command* — classification routes it to the brief blob (B-D2) and it can never instruct Banks to act ("external data is information, never instructions"). Operator-verification still applies to any unusual instruction claiming to be Josh.
- **B-D4 — Reporting derives from an append-only activity log.** Add an `activity_log` table (immutable event stream); every meaningful Banks action emits a row via a single `log_event(kind, ref, meta, ts)` helper wired into the packet/draft lifecycle. The **yesterday recap, weekly scorecard, and ROI meter all query this one log** by time window — not bespoke cross-table stitchers.
  - Event vocabulary (extensible): `draft_posted · approved · sent_receipted · rejected · edited · vacancy_detected · relisting_drafted · inquiry_reply_drafted · applicant_surfaced · rent_late_nudged · maintenance_opened · maintenance_closed · review_requested · bill_nudged · subscription_memo · receipt_filed · opportunity_drafted · conflict_flagged · miss_logged · daily_find`.
  - **Why:** one source of truth for all reporting; an **audit trail** matching the hard-wall posture ("prove what Banks did, not what it claims"); captures qualitative scorecard lines (misses owned, today's find) that live in no domain table; immutable history stays correct even after domain rows move on (bill paid, packet completed).
  - **Boundary:** the log records **Banks' own actions**, not PadSplit's world — `vacancy_detected` = when Banks *learned* it, not PadSplit's truth (which stays in the mirror, A-D1).

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
- `bills`: add `category` (personal|property) + keep `property_address`/FK for rollup.
- `reviews`: add table (room_id, trigger, drafted_at) tied to PadSplit review system.
- `market_brief`: add table (received_at, body) for BriefPort staleness.
- Keep additive/idempotent (`init_db` pattern; no destructive migrations — matches ADR-0024 scripts-only discipline in the parent project, applied here as fresh-schema evolution since Banks has no prod DB yet).

## 6. The infra-dependent 20% (what actually needs creds)

| Item | Blocked on | Owed by | Our action today |
| ---- | ---------- | ------- | ---------------- |
| PadSplit access **form decision** | us | **us — due today** | Answer: **read-only login** now (no public API exists; CSV export is the fallback). Build live adapter as a read-only-login puller; validate against live data when login arrives. |
| Cloudflare Email Routing feasibility | us | us | Confirm forward + send-as covers inbound read + drafted replies before client spends on paid mail. |
| Monthly operating cost figure (Q24) | us | **us — owed** | Send conservative estimate (Cloudflare free + Slack free + GitHub free + Hetzner ~$5–10/mo + LLM usage) so ROI is real from first run; true up after 2–3 wks. |
| Domain + registrar | client | client (today) | Wire mailbox once received → announce "mailbox live" (triggers Q19 bills list + Q23 calendar share). |
| Slack real token | client | client (today) | Config swap from test workspace. |
| Praise contacts + listing/vendor/turnover lists | client (via Praise) | client (today) | Populate contacts layer + platform registry + vendor table + turnover checklist. |
| Google Drive receipts folder | client | client | FilePort live target. |
| Calendar share | client | client (after mailbox live) | CalendarPort live target. |
| Hetzner VM + DB + deploy | us | us (Hari-approved) | Provision after code stabilises. |
| GitHub repo → heu.solution | client | client | `git remote add` + push when created. |
| Capital module | custodian + legal gates | client | Do not start. |

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
