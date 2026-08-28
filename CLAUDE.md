# Banks — Project Instructions & Progress

Banks is Josh Kantor's personal AI employee for job-search automation. It is
**hard-walled** from the Forced Action (FA) platform and **drafts-only**.

## Non-negotiable rules (enforced by tests, not convention)

1. **Hard wall.** No FA imports, no FA env vars, no shared DB. `test_hardwall.py`
   asserts this. FA credential markers (STRIPE, FA_, INSTANTLY, …) must never be
   set in Banks' environment. See `banks/config.py:FA_FORBIDDEN_ENV_MARKERS`.
2. **Drafts only.** Banks never sends, posts, submits, pays, or transacts. Every
   output is a Decision Packet surfaced to Slack awaiting Josh's approval.
   `opportunities.submitted` is always 0 by construction (`enforcement.py`).
3. **No embellishment, ever.** Application/outreach drafts pull facts *only* from
   `banks/memory/career-facts.md`. If a fact isn't there, flag the gap — never
   invent. `draft_application()` raises `UnknownCareerFact` on an empty file.
4. **Config via `load_config()`** (`banks/config.py`) — never read `os.environ`
   directly in domain code.
5. **Port pattern** for every external service: a `Fake*` (in-memory, no network,
   used by tests) and a `Live*` behind a `Protocol`. Domain code depends on the
   protocol, never the SDK.

## Storage

Single-file **SQLite** (`banks.db`), WAL mode — deliberate: one user, tiny data,
and no connection string means the FA wall is *physical*. No DB server needed.
`db.cursor()` for one read/write; `db.transaction()` for atomic multi-row writes.
Schema: `banks/store/schema.sql` (idempotent `CREATE TABLE IF NOT EXISTS`).

## What the client wants (the whole build as a user story)

Josh Kantor is a senior GTM exec (VP Sales / CRO) hunting his next role
(full-time / contract-to-hire / fractional / consulting). LoopCV & Simplify
already create application **volume**; Banks owns the **intelligence,
relationship, and multi-channel surround layer after** the application. The
application alone rarely gets a reply — interviews come from the *surround*:
reaching the real hiring manager, a warm intro through his own network, the
right recruiter, follow-ups on the right day, never letting a live conversation
go cold or double-contacting a company.

- **MOD-01 — "make sense of my raw application flow":** pull in Simplify/LoopCV
  exports, a pasted URL, or "I applied here"; dedupe; Tier A/B/C; pursuit mode.
- **MOD-02 — "find the actual human + who can introduce me":** real req owner
  (not HR), verified email or LinkedIn fallback, and who he already knows there.
- **MOD-03 — "surround it, in my voice, honestly":** on a Tier A, the whole
  coordinated pack at once (hiring-manager / LinkedIn / recruiter / warm-intro /
  employee), POV briefs, no-role & consulting pitches. Facts ONLY from
  career-facts — never invent.
- **MOD-04 — "chase properly, never look desperate":** Day 3/7/14 then stop;
  instant reply-stop; freeze a company when a conversation opens; rate caps
  (20 LinkedIn, 40 email/day, 14-day spacing); funnel tracking.
- **MOD-05 — "run my day from Slack, one tap":** morning Daily Attack Queue;
  Approve/Skip/Snooze + mark manual done; threaded NL revisions ("shorter");
  on-demand lookups ("who do I know at X", company status, call list).
- **MOD-06 — "never contact the wrong people, then go live":** adversarial
  exclusion at draft + send time; full mock run; ship to his private GitHub.

**The spine through all six:** Banks *prepares and proposes*; Josh *decides and
approves*. Nothing is ever sent/posted/submitted/paid without his one-tap
approval. No autonomous sends, no standing orders.

## Maximum Distribution Build — module status

| Module | Scope | Status |
|---|---|---|
| MOD-01 | Application intake, dedup, fit scoring, tiering | **Build-complete, e2e-tested, live. Forwarded email listener built (LiveImapEmailPort + scheduler job email_intake_poll every 10 min). Needs BANKS_INTAKE_EMAIL + BANKS_INTAKE_EMAIL_PASSWORD in .env.** |
| MOD-02 | Contact resolution, enrichment, warm-path graph | **Build-complete, e2e-tested; Clay enrichment needs paid plan** |
| MOD-03 | 7 distribution lanes & surround workflow | **Build-complete, code-reviewed. LinkedIn DM deep-link handoff wired into linkedin + employee lanes. 490 tests green.** |
| MOD-04 | Follow-up cadence, governance, collision ledger | **Build-complete, code-reviewed, 460 tests green** |
| MOD-05 | Slack command & control (Daily Attack Queue) | **Build-complete; Approve/Skip/Snooze/Mark-done + Revise ALL proven LIVE in test ws (2026-08-28). Event Subscriptions enabled. Network Activation Lite (Tier A/B-tied contacts, channel suggestion) + No-Open-Role Lite (consulting pitch for warm-contact companies with no active opp) both built + tested.** |
| MOD-06 | Adversarial exclusion & launch staging | **Build-complete; two-gate exclusion (intake + send-time); person/company/indirect/conduit exclusion; Slack CSV upload proven LIVE (2026-08-28). 473 tests green. Pending: Josh's full exclusion list.** |

### MOD-01/02 key modules (all unit-tested; 460 tests green across the suite)
- `banks/intake.py` — the orchestration seam. `ingest_simplify()` runs
  parse→exclude→dedup→normalise→classify→score→tier→record; `ingest_contacts()`
  loads + merges the contact graph; `export_enrichment_queue()` writes the manual
  Clay CSV.
- `banks/manual_intake.py` — Manual Intake Surface. `ingest_manual()`
  accepts a pasted JD (comp via `extract_comp_k()` regex + industry via LLM →
  fully scored, can reach Tier A/B and surface now), a URL, or a quick
  "I applied here" (held for enrichment). CLI: `python -m banks.manual_intake`.
- `banks/csvport.py` — Fake/Live CSV port + parsers (Simplify, LinkedIn
  connections w/ 3-line preamble skip, alumni, recruiter, LoopCV stub).
  Columns confirmed against Josh's real export files (2026-08-25).
  LoopCV parser is built but dormant — needs Josh's real export to confirm columns.
- `banks/dedup.py` — 2-pass opportunity dedup (URL exact → fuzzy slug) +
  `find_duplicate_contact()` by LinkedIn URL.
- `banks/normalise.py` — company normalisation (whitespace-collapse evasion fixed),
  pursuit-mode classifier, Simplify status mapper.
- `banks/score.py` — fit scorer with `ScoringConfig` dataclass. Weights
  **Comp 35 / Vertical 25 / Geo 20 / Pursuit 20**. Tiers **A≥75, B 50–74, C<50**.
  Comp floor $150k, sweet spot $220k+. Unknown comp/vertical → 0.5 neutral.
- `banks/exclusion.py` — company + person + indirect + conduit exclusion.
  `is_target_excluded()` is the single unified predicate wired at all 3 gates
  (intake, surround, relay). Whitespace-collapse evasion fixed. Rent Solutions seeded.
- `banks/contact_enrichment.py` — MOD-02 verified contact enrichment. Batch
  `EnrichmentPort` (submit/retrieve): Fake, ManualCSV ($0 interim), LiveClay
  (paid, inert until upgraded). Cold Tier A/B opportunity → `enrichment_queue`.

### MOD-03/04 key modules (build-complete, reviewed)
- `banks/surround.py` — `generate_surround_pack()`: Tier A → full surround pack;
  Tier B → recruiter only. Posts each lane as a separate Slack card.
  Company-freeze check before generating. MOD-06 exclusion gate wired: passes
  `company`/`contact` to `flow.propose` so the chokepoint actually fires.
  Warm-intro state machine (ASKED→AGREED→INTRODUCED, auto-STALL 7 days).
- `banks/lanes.py` — 7 lane drafters: hiring_manager, recruiter, employee,
  warm_intro, linkedin, pov_brief, consulting. All facts-only, LLM optional.
  Empty career-facts → raises ValueError.
- `banks/governance.py` — Daily caps (email 40, LinkedIn 20, overflow-safe);
  `got_reply()` atomically freezes company + cadence; `queue_cadence()` Day 3/7/14
  keyed off `outreach_lanes.sent_at`; `due_cadence_touches()` stops on
  interviewing/closed; `network_activation_due()`; `weekly_funnel_summary()`;
  `check_14day_spacing()` by contact_id.
- Schema additions: `outreach_lanes`, `warm_intros`, `cadence_queue`,
  `governance_ledger`, `company_freeze`, `funnel_events`.

### MOD-05/06 key modules (build-complete; full grill in docs/decisions/BUILD_DECISIONS_MOD03-06.md)
- `banks/attack_queue.py` — pure `build_sections()` (failure-mode-first order,
  empty-omit, career-facts blocker line, score ranking, imported digest, funnel
  footer) + `post_daily_queue()` (exactly-once via `daily_queue` date-claim;
  cards threaded under a summary root; carried-over items re-posted live).
  Uses `Card` dataclass for type-safe card construction.
- `banks/queue_actions.py` — `snooze_item` / `skip_item` / `mark_done`
  (MARK_SENT semantics → touch_log + cadence + funnel). All three wired to
  card buttons (Skip/Snooze/Mark-done). KNOWN DEFECT: `mark_done` uses 6
  separate `cursor()` calls — should be one `transaction()`. Not yet fixed.
- `banks/commands.py` — hybrid intent router (keyword fast-path → LLM
  `extract_json` fallback), `handle_command` (whoat / status snapshot /
  calllist / help), `RateLimiter`.
- `banks/revisions.py` — button-driven pending slot (`set/get/clear_pending_revision`,
  15-min expiry, last-tap-wins). `apply_revision()` facts-only prompt +
  embellishment post-check → `redraft` in place. `load_career_facts()` feeds
  real facts from `career-facts.md`. DEAD CODE: `is_revision_context` and
  `classify_revision` are unused (old thread-reply approach) — can delete later.
- `banks/socket_listener.py` — live loop. `classify_incoming()` is the single
  precedence source: halt → pending-revision → command → ignore. Kill-switch
  fires on ANY human message including edits (halt evaluated before bot/subtype
  filter). Slack CSV upload (`_handle_file`) downloads Simplify exports dropped
  in #banks-jobs and runs `ingest_simplify`. Single-approver lock via
  `is_authorized`. Needs **Event Subscriptions** (`message.channels`) enabled
  on the Banks Slack app.
- `banks/slackfiles.py` — leaf adapter for authed Slack file download (Bearer
  token). Confined here per hard wall. Hardwall-allowlisted.
- `banks/exclusion.py` — extended with person exclusion (LinkedIn URL / normalized
  name), indirect (corporate variants), conduit (contact at excluded firm).
  `load_exclusions_from_file()` called at startup from `container.py`.
- `banks/relay.py` — send-time exclusion gate + `check_halt()` at entry.
  `RelayResult.blocked` list. The only place that sends; holds the only outbound
  credential.
- `banks/mailer.py` — `SmtpMailer` (stdlib smtplib, STARTTLS) + `load_mailer(cfg)`
  factory. SMTP reachable in prod via `container.py` (previously dead — fixed).
- `banks/flow.py` — `propose()` enforces exclusion wall at every-draft chokepoint
  via `company`/`contact` params. Raises `DraftExcluded` when target is blocked.
- Schema additions: `queue_items`, `daily_queue`, `person_exclusions`,
  `pending_revisions`.
- Live proof: `scripts/mod05_smoke_test.py` posts a real queue to TEST ws;
  Approve/Skip/Snooze/Mark-done verified live end-to-end (DB state confirmed).

### Locked decisions (2026-08-25)
- **Decision 4 (surface policy):** Simplify has no salary/industry → tiering is
  half-blind → rows recorded with `needs_enrichment=1` and **NOT surfaced** to
  Slack (would flood as Tier B). Surfacing waits until enrichment fills
  comp+vertical and score is recomputed.
- **Decision 5 (contact merge):** all 64 alumni + 8 recruiters overlap the 1,694
  LinkedIn connections. Ingestion **merges** on LinkedIn URL and upgrades the
  source label (recruiter > alumni > linkedin), backfilling title/vertical_fit/
  notes/position — never a bare skip.
- **Decision 6 (Clay is manual):** Clay's free tier blocks webhook + HTTP API +
  Google Sheets + own-key (all paywalled ≥$134/mo as of Clay's 2026 pricing).
  The old `/v1/sources/enrichment` endpoint returns 404 "deprecated" (verified).
  Path: `ManualCSVEnrichmentPort` writes `needs_enrichment.csv`, Josh runs it
  through Clay's UI (≤200 rows / 100 credits/mo), drops the enriched file back;
  `LiveClayEnrichmentPort` (batch push+poll) is inert until a paid plan lands.

## Open blockers to going live

### Our side (we can fix now)
- Production startup must go through `Container.live()` (calls
  `load_exclusions_from_file`) — raw `init_db` skips exclusion seeding. Already
  correct in the listener; test scripts need the same.

### Remaining spec gap
- **Hunter.io / Anymail Finder** — spec names these as enrichment providers.
  No code exists. Clay manual-CSV is the interim. Needs paid API key from Josh
  before a `LiveHunterPort` is worth building.

### Client-gated (waiting on Josh / CTO)
- **`career-facts.md` is empty** — Josh's resume facts must be added before any
  outreach draft is useful. Biggest content blocker.
- **Full exclusion list** — only "Rent Solutions" seeded in `exclusions.txt`.
- **Paid Clay / Hunter.io** — for hands-off contact enrichment. Manual CSV
  (`ManualCSVEnrichmentPort`) is $0 interim.
- **Josh's Gmail app password (outbound)** — for live email send via `SmtpMailer`.
- **banks-intake@gmail.com app password** — for `LiveImapEmailPort` (email intake).
  Set `BANKS_INTAKE_EMAIL` + `BANKS_INTAKE_EMAIL_PASSWORD` in `.env`.
- **LoopCV export** — `parse_loopcv_row` is built but dormant; needs Josh's real
  export file to confirm column names.

### Live-tested (2026-08-28, test workspace)
- ✅ Approve / Skip / Snooze / Mark-done buttons
- ✅ Revise (button-driven NL revision, Event Subscriptions confirmed working)
- ✅ CSV upload — Simplify export dropped in #banks-jobs, receipt posted, 4 rows
  ingested as Tier B held-for-enrichment, Rent Solutions excluded
- ✅ Kill switch (`stop all` halts all jobs)
- ✅ Idempotency — second queue post same day skipped
- ✅ LinkedIn DM deep-link (Gap 2) — card shows compose link, Mark done confirmed working
- ✅ `files:read` scope added to Banks Slack app (CTO done)

### Launch / infra
- Live end-to-end acceptance run (7-item signed checklist in `docs/launch/LAUNCH_ACCEPTANCE.md`).
- Production server (Hetzner) — provisioning + deploy.
- Merge stack to `main` — PRs open on feature branches; stack must merge bottom-up.

## Testing
`python -m pytest tests/ -q` — **485 passing**. Every new external adapter must be
added to `test_hardwall.py`'s allowlist and prove no FA imports.

## Git
Work on feature branches, PR to `main`. Never push without Lesly's explicit
permission. Repo: github.com/jbkcreator/Banks.
