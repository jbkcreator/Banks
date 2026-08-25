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

## Maximum Distribution Build — module status

| Module | Scope | Status |
|---|---|---|
| MOD-01 | Application intake, dedup, fit scoring, tiering | **Build-complete, e2e-tested, live** |
| MOD-02 | Contact resolution, enrichment, warm-path graph | **Build-complete, e2e-tested; Clay enrichment needs paid plan** |
| MOD-03 | 7 distribution lanes & surround workflow | **Build-complete, code-reviewed, 358 tests green** |
| MOD-04 | Follow-up cadence, governance, collision ledger | **Build-complete, code-reviewed, 358 tests green** |
| MOD-05 | Slack command & control (Daily Attack Queue) | Not started — needs dedicated Banks Slack app |
| MOD-06 | Adversarial exclusion & launch staging | Not started — needs Josh's full exclusion list |

### MOD-01/02 key modules (all unit-tested, 358 tests green)
- `banks/intake.py` — the orchestration seam. `ingest_simplify()` runs
  parse→exclude→dedup→normalise→classify→score→tier→record; `ingest_contacts()`
  loads + merges the contact graph; `export_enrichment_queue()` writes the manual
  Clay CSV.
- `banks/manual_intake.py` — Manual Intake Surface (plan line 98). `ingest_manual()`
  accepts a pasted JD (comp via `extract_comp_k()` regex + industry via LLM →
  fully scored, can reach Tier A/B and surface now), a URL, or a quick
  "I applied here" (held for enrichment). CLI: `python -m banks.manual_intake`.
  This is the path that unblocks real tiering without Clay.
- `banks/csvport.py` — Fake/Live CSV port + parsers (Simplify, LinkedIn
  connections w/ 3-line preamble skip, alumni, recruiter). Columns confirmed
  against Josh's real export files (2026-08-25).
- `banks/dedup.py` — 2-pass opportunity dedup (URL exact → fuzzy slug) +
  `find_duplicate_contact()` by LinkedIn URL.
- `banks/normalise.py` — company normalisation, pursuit-mode classifier, Simplify
  status mapper.
- `banks/score.py` — fit scorer. Weights **Comp 35 / Vertical 25 / Geo 20 /
  Pursuit 20**. Tiers **A≥75, B 50–74, C<50**. Comp floor $150k, sweet spot
  $220k+. Unknown comp/vertical → 0.5 neutral.
- `banks/exclusion.py` — company-only exclusion (former employees still
  contactable if moved on). Rent Solutions seeded.
- `banks/contact_enrichment.py` — MOD-02 verified contact enrichment. Batch
  `EnrichmentPort` (submit/retrieve): Fake, ManualCSV ($0 interim), LiveClay
  (paid, inert until upgraded). Cold Tier A/B opportunity → `enrichment_queue`.
  (The old `clay_port.py` was deleted — superseded by this.)

### MOD-03/04 key modules (build-complete, reviewed)
- `banks/surround.py` — `generate_surround_pack()`: Tier A → full surround pack; Tier B → recruiter only. Posts each lane as a separate Slack card. Company-freeze check before generating. Warm-intro state machine (ASKED→AGREED→INTRODUCED, auto-STALL 7 days).
- `banks/lanes.py` — 7 lane drafters: hiring_manager, recruiter, employee, warm_intro, linkedin, pov_brief, consulting. All facts-only, LLM optional. Empty career-facts → raises ValueError.
- `banks/governance.py` — Daily caps (email 40, LinkedIn 20, overflow-safe); `got_reply()` atomically freezes company + cadence; `queue_cadence()` Day 3/7/14 keyed off `outreach_lanes.sent_at`; `due_cadence_touches()` stops on interviewing/closed; `network_activation_due()`; `weekly_funnel_summary()`; `check_14day_spacing()` by contact_id.
- Schema additions: `outreach_lanes`, `warm_intros`, `cadence_queue`, `governance_ledger`, `company_freeze`, `funnel_events`.

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
  `LiveClayEnrichmentPort` (batch push+poll) is inert until a paid plan lands
  (CLIENT_QUERIES_V2 item 5). Lean on LinkedIn-export emails first.

## Open blockers to going live

- **MOD-01:** None — e2e tested and live.
- **MOD-02:** Paid Clay/Hunter.io for hands-off contact enrichment. Manual CSV path ($0) works in the interim.
- **MOD-03:** `career-facts.md` must be populated (Josh's resume); domain + from-email for email lane sending.
- **MOD-04:** Dedicated Banks Slack app for Interview/Offer button wiring.
- **MOD-05/06:** Not started — see module status table above.

_Deferred (not blocking):_ Hetzner production server (24/7 deploy, MOD-06); LoopCV export (Simplify-only at launch).

## Testing
`python -m pytest tests/ -q` — **358 passing**. Every new external adapter must be
added to `test_hardwall.py`'s allowlist and prove no FA imports.

## Git
Work on feature branches, PR to `main`. Never push without Lesly's explicit
permission. Repo: github.com/jbkcreator/Banks.
