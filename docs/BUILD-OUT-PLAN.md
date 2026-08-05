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

**Phase C design decisions (grilled 2026-08-05):**
- **C-D1 — Three-party routing: Banks drafts → Josh approves → Relay delivers to Praise → Praise sends to tenant/vendor.**
  - **Recipient settled by client** (verbatim): "Banks will often be drafting to Praise rather than to a tenant or vendor directly"; "draft replies for Praise to send, or route to him." So for all **tenant/vendor-facing** rental comms, `Draft.to = Praise`; the draft *body* is the message Praise relays. Banks and Josh never contact tenants directly — Praise is the executor.
  - **Two destination classes:** *Josh-facing* (morning briefing, rate memos, finance items, all approvals) go to Josh; *Praise-facing* (inquiry replies, vendor outreach, turnover coordination) end at Praise.
  - **Approval gate = Josh-always (C-D1a).** Every outbound — including routine Praise-facing drafts — goes to `#banks` for Josh's ✅ before Relay delivers to Praise. Faithful to the immutable core ("everything through Josh's tap"; only Josh may widen it by amendment). We do **not** quietly grant Banks a Praise-direct path.
  - **FLAG TO JOSH (autonomy question):** routine tenant inquiry replies may be high-volume; ask whether he wants to authorize a **Praise-direct fast-path for routine cases** (a conscious standing-order widening he approves), keeping only unusual/high-stakes items on his gate. Until he says so, Josh-always holds.
  - **BLOCKED:** the *channel* to reach Praise (email vs shared Slack) is pending Praise's contact details (client "connecting you and Praise today"). The contacts layer is built now against a fake Praise; live channel wired when details arrive.

- **C-D2 — Applicants: surface + draft downstream; and the READ-ONLY-PADSPLIT invariant (locked).**
  - Banks **surfaces** each PadSplit-presented applicant in `#banks` (name + PadSplit's own screening summary, verbatim — no independent scoring, per Q13), Josh decides, and **Josh/Praise click approve/decline inside PadSplit**. The decision is always a human, in-platform action ("every approval remains mine").
  - Banks then **drafts the downstream communication** (decline note or welcome/next-step) *for Praise to send*, routed through C-D1 (Josh ✅ → Relay → Praise). The decision stays human; only the resulting message is drafted.
  - **INVARIANT (locked): SourcePort is READ-ONLY. Banks never automates any write to PadSplit — ever.** All PadSplit state changes (applicant approve/decline, listing edits, rent, reviews) are human, in-platform actions. Automation/Relay touches only comms channels (email/Slack), never PadSplit. Writing to his live income-bearing platform is a different, forbidden risk class from reading it, and drafts-only forbids "submit" outright. The hard-wall harness asserts no PadSplit-write path exists.
- **C-D3 — Turnover: locked constraints only; detailed design BLOCKED on Praise's checklist + a Josh clarification.** Client Q17 settles little and we won't over-design:
  - **Locked:** the turnover checklist is **Praise's, not ours** (we don't design the steps); turnover is **room-level in an occupied co-living house** (cleaning/repairs/showings happen around sitting tenants); drafted comms must **"account for housemates not party to the turnover."**
  - **BLOCKED on Praise's checklist** (pending from Praise) — it defines the steps Banks coordinates. No turnover step logic is built until it arrives; the fixture uses a placeholder checklist only.
  - **FLAG TO JOSH (clarification):** "account for housemates" is ambiguous — does he want Banks to (i) **draft heads-up notices to housemates** about shared-space activity, or (ii) simply **keep housemates out of** the turnover comms (privacy/consideration)? Do not assume; ask. Digest-vs-per-event and whether housemate comms exist at all depend on his answer.
  - Housemate identification, if needed, derives from the property→rooms mirror (occupied rooms at the address minus the turning room) — no new data.
- **C-D4 — Review requests: triggers (client-given) + detection + draft→Praise routing.** Reviews go through **PadSplit's own review system** (Google integration dropped entirely, per Q16). Triggers and their detection:
  - **Maintenance resolved promptly** — auto-detected from Banks' maintenance state machine (closed within N days of opening).
  - **Smooth move-in** — auto-detected via proxy from the mirror: move-in occurred + no maintenance/complaint in the first N days.
  - **Unprompted appreciation** — **not detectable by Banks** (tenants talk to Praise, not Banks, per C-D1). Kept alive by **relay**: Praise/Josh drops a line in `#banks` ("Room 3 tenant thanked us"), which B-D3 classification routes as a review-trigger signal.
  - **Payment-streak trigger** — configurable, **off by default** (Q16).
  - **Request routing:** Banks is read-only on PadSplit (C-D2), so it **drafts the review request → routes via C-D1 (Josh ✅ → Praise)**, and Praise triggers it in PadSplit's review system. Banks never touches PadSplit's review button.

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

**Grilled decisions (Phase D):**

- **D-D1 — Forwarded email → bill/receipt: extraction + property attribution.** Client (Q19/Q20) settled the *shape*: bills via forwarded renewal emails + `#banks` notes, two categories (personal vs property, property rolls up per address); receipts filed per-property in a Drive folder he owns, personal folder for unattributable, **preserve original attachment**. Client did NOT specify the mechanics. Decision:
  - **Extraction = LLM.** Banks reads the email body/attachment and pulls amount / due-date / cadence / vendor; handles arbitrary formats. Never silently trusted — every extracted bill/receipt is shown as a draft and confirmed at Josh's ✅ before it becomes a tracked record (extraction error caught at the tap, not after). Rules/regex rejected (brittle across unknown senders).
  - **Attribution = infer-then-confirm (hybrid).** Banks guesses personal-vs-which-property by matching address/property name in the email against the property list from the PadSplit mirror, shows its guess in the draft ("filing under 123 Main — correct?"), Josh confirms/corrects at the ✅. **Unattributable → personal folder** (client's stated default). Same confirm-on-ambiguity discipline as B-D3; keeps property rollups (expense/tax substantiation) accurate rather than silently mis-filed. Attachment bytes preserved verbatim via FilePort regardless of extraction.

- **D-D2 — Opportunity posting discovery = forward-driven; automated discovery flagged to Josh.** Client (Q21) named sources (LinkedIn + PropTech boards + networks) but LinkedIn has no open job-search API — automated discovery is scrape-class on Josh's *real* LinkedIn identity, high-risk for ~2-3 relevant VP roles/month. Decision: **built path = Josh forwards a posting to `banks@`** (mirrors D-D1 forward pattern, zero scrape risk, available day one). The valuable pipeline — parse posting → match against resume v14 → **flag gaps (never write around)** → draft application (never submit, show posting first, per Q21) — is identical regardless of how the posting arrives, so it's built now against fakes (part of the 80%). **Automated LinkedIn/board discovery is flagged to Josh, not built**; if he wants it, it becomes a recon spike like PadSplit's — scrape ToS/account-risk needs his sign-off first. Resume v14 parsed once into `career-facts` memory; `UnknownCareerFact` guard (already built) enforces no-embellishment. **Interview briefs flagged to Josh, not built** — the feature came from our original questionnaire, not Q21; "interview" absent from client's answer. Drop from built scope until Josh confirms he wants interview-prep briefs.

- **D-D3 — Schedule: conflict definition + ROI meter cost side.** Client (Q23) settled read-only calendar (build must prove write is *structurally* unavailable), personal/family blocks = real conflicts (equal weight), occasions on shared calendar; Q24 = $48/hr, monthly cost owed by us. Mechanics decided:
  - **Conflict = hard wall-clock overlap** (deterministic, no location guessing). Personal blocks treated identically to business per Q23. **Travel/buffer-aware detection flagged to Josh, not built** — needs location data the calendar may not carry; add later if his events carry locations.
  - **ROI meter:** weekly net = hours-saved × $48 − (our monthly operating cost ÷ 4.33). **Hours-saved derived from `activity_log` (B-D4)** completed events — each action type carries a standing minutes-saved estimate, tuned over time, **rounded down** (client's "can't flatter itself" intent). Cost figure still owed by us (§6).

- **D-D4 — Bill nudge timing + never-pays.** Client (Q19) didn't specify cadence. **Fixed 7-day + 1-day before due** (due-date from D-D1 extraction); a past-due unpaid bill **escalates into the morning brief failure-mode-first block (B-D1)**. **Never-pays stays structural** — `finance.py` has no pay path; Banks drafts a reminder *to Josh*, never a payment. Amount-scaled cadence rejected (over-engineered for predictable value).

**Phase D COMPLETE** — D-D1..D-D4 decided. Open Josh-facing flags from this phase: automated posting discovery (D-D2), travel-buffer conflict detection (D-D3), interview briefs (D-D2). Capital (Q22) remains FROZEN, out of Phase D.

### Phase E — Slack live path (uses test workspace — part of the 80%)
- ChatPort live adapter against a **test Slack workspace**: post drafts, read reactions for two-step approval (✅ approve → then "sent" mark). Prove the real interaction, not just outbox. Swap to the client's real workspace token later = config change only.

**Grilled decisions (Phase E):**

- **E-D1 — Reaction/message ingestion = polling (pull), behind ChatPort.** Slack Events API needs a public inbound URL (no server until Phase G), so it can't run against the test workspace now. Decision: **poll `conversations.history` + `reactions.get` on the existing scheduler cadence** — outbound token only, runs today. Maps reads to A-D8 draft_ref correlation + 4-reaction vocab (✅📤❌✍️). If real-time ever matters, an Events-API adapter swaps in behind the same port, no domain change.
- **E-D2 — TEST workspace wired & verified (2026-08-05).** Slack app "Banks" created in workspace **"bank test"** (`T0BNYH0JSSC`), bot user `banks` (`U0BN4F05R0S`). Bot scopes: `chat:write`, `groups:history`, `groups:read`, `reactions:read`. Private channel **`banks_test` = `C0BN4GKHJCS`**, bot is a member. `auth.test` + `conversations.list` (private) verified live. Creds in **git-ignored `.env`** (`BANKS_SLACK_BOT_TOKEN`, `BANKS_CHANNEL_ID`) — read by `config.load_config()`; **never committed**. Real-workspace swap later = change those two values only. App-level `xapp-` token (socket mode) stored but unused by the polling adapter.

### Relay design (cross-cutting — realizes A-D9)

The deterministic non-agent executor that auto-sends exactly what Josh approved on ✅. The LLM agent never holds send capability.

**Grilled decisions (Relay):**

- **R-D1 — Send capability is structurally isolated: separate process + credential isolation + static harness test.** The send credential (Cloudflare send-as / SMTP token) lives **only in the Relay process's environment**, never loaded into the agent process. The agent writes an **approved-send intent row** to the store; Relay is the sole reader of intents and sole holder of the credential — the agent has no token to send with even if prompt-injected/compromised. Backed by a **static AST harness test** (mirroring the FA hard-wall test) asserting the agent package never imports the send/SMTP client and never reads the send-credential env var. Discipline-only same-process rejected (policy, not structure — same reason drafts-only is structural). The store's approved-intent row is the only channel agent→Relay.

- **R-D2 — Relay sends a frozen payload snapshot, idempotent per draft_ref.** The approved-intent row carries the **exact bytes rendered into `#banks` and approved** (recipient, subject, body, attachments) — a snapshot, not a draft_ref to re-read (eliminates any drift between what Josh saw at ✅ and what goes out). draft_ref rides along for correlation/audit only. **Idempotency:** a `sent_receipts` table with a **unique constraint on draft_ref** — Relay claims the intent (`status=sending`) before sending; a duplicate ✅ from the poll loop hits the constraint and no-ops. Success → `status=sent` + provider message-id stored. Failure → row stays `failed`/`sending` → surfaces in the **approved-but-unsent aging queue** (Q6 / B-D1 morning brief) → selfheal retry.

- **R-D3 — Every draft carries an explicit `send_channel`; Relay acts only on outbound channels.** Set when the draft is *created*, never inferred at send time: `email:praise` (tenant/vendor-facing — routes through Praise per C-D1), `email:sendas` (Josh's own outbound correspondence), or `none:internal` (bill nudges, morning-brief items — informational to Josh, already in `#banks`). Relay **no-ops on `none:internal`** — a ✅ there means "acknowledged/handled," not "transmit." Content-inference at the send boundary rejected. **Tenant/vendor-facing is structurally `email:praise`** — direct-to-tenant send is disallowed (locks C-D1); enabling a direct-to-tenant channel is the autonomy fast-path already flagged to Josh (C-D1), not a default.

- **R-D4 — Poll loop is Relay's sole trigger; ✅/📤 reconciled to prevent double-send.** The polling loop (E-D1) is the *only* thing that writes intents — **Relay never reads Slack** (keeps it credential-isolated per R-D1 and deterministic). Reconciliation against the A-D8 vocab: **✅** on an `email:*` draft → intent `approved` → Relay sends → on success marks item `sent` **and posts 📤 back into `#banks`** so the thread reflects reality. **📤 by Josh first** (he sent it himself) → item marked `sent` by his hand → intent **suppressed**, Relay never fires (idempotency receipt also guards). **❌ / ✍️** → no intent created. Two-step (Q6) stays honest; no double-send.

**Relay design pass COMPLETE** — R-D1..R-D4 decided.

### Phase F — Integration, acceptance, hardening
- End-to-end "day in the life" runs against all fakes: seeded vacancy→Praise draft; seeded late payment→nudge; seeded PadSplit-presented applicant→surfaced; seeded bill→nudge; seeded calendar conflict→flag; seeded resume+posting→application draft with a gap flagged.
- Re-run and extend the **FA hard-wall harness** (must stay green through all rework — no FA import/env/query path ever creeps in).
- Target: full suite green; ~80% of total planned code complete.

**Grilled decisions (Phase F):**

- **F-D1 — Acceptance set = 8 day-in-life scenarios (all against fakes, end-to-end through ports + Relay) + 2 hard-blocking gates.** Scenarios: (1) vacancy→Praise draft→✅→Relay `email:praise`; (2) late payment→nudge, observe-only; (3) PadSplit-presented applicant→surfaced (no independent screening); (4) bill→7+1 nudge, `none:internal` Relay no-op on ✅; (5) calendar conflict→flag (personal block = real conflict); (6) resume+forwarded posting→application draft with gap flagged (`UnknownCareerFact` fires); (7) two-step loop ✅ auto-send+auto-📤, manual 📤 suppresses Relay; (8) morning brief failure-mode-first + approved-but-unsent aging on top. **Hard-blocking gates (a red here fails the phase regardless of scenarios):** FA hard-wall harness green (extended with R-D1 send-isolation AST test) + constitution SHA-256 integrity halt-on-tamper green. All green = **80% build done**.

**Phase F COMPLETE** — F-D1 decided.

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
