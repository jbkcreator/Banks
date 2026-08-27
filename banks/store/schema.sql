-- Banks data store. Banks-local, hard-walled. No FA tables, no FA foreign keys, ever.
-- Column shapes derive from Part 5's standing jobs + weekly scorecard, not client
-- data — safe to build and seed before real rental/finance sources are provisioned.

PRAGMA foreign_keys = ON;

-- Standing job 2/3: rooms first, property ops.
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    property_address TEXT NOT NULL,
    unit_label TEXT NOT NULL,             -- e.g. "Room 3" or "Unit A"
    rented_by_room INTEGER NOT NULL,      -- 1 = per-room, 0 = whole-unit
    current_rent_cents INTEGER,
    occupied INTEGER NOT NULL DEFAULT 0,
    tenant_name TEXT,
    lease_start TEXT,                     -- ISO date
    lease_end TEXT,                       -- ISO date
    vacancy_signal_at TEXT,               -- when Banks learned it went vacant
    days_vacant INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY,
    room_id INTEGER REFERENCES rooms(id),
    received_at TEXT NOT NULL,
    replied_at TEXT,
    score INTEGER,                        -- pre-scoring result
    applied INTEGER NOT NULL DEFAULT 0,   -- did it convert to an application?
    source TEXT                           -- which inbox/channel it arrived on
);

-- Standing job 3: property ops.
CREATE TABLE IF NOT EXISTS maintenance_tickets (
    id INTEGER PRIMARY KEY,
    room_id INTEGER REFERENCES rooms(id),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    vendor_name TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open'   -- open | vendor_drafted | closed
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    trade TEXT,
    contact TEXT,
    preferred INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

-- Standing job 4: money truth. Track & remind only — never a payment table.
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount_cents INTEGER,
    due_date TEXT NOT NULL,
    cadence TEXT NOT NULL,                -- monthly | annual | one_time | ...
    property_address TEXT,
    -- Q19: tag personal vs property-level from the start; property-level bills
    -- roll up per property for expense tracking. 'personal' | 'property'.
    bill_category TEXT NOT NULL DEFAULT 'personal',
    is_subscription INTEGER NOT NULL DEFAULT 0,
    keep_kill_candidate INTEGER NOT NULL DEFAULT 0,
    keep_kill_memo TEXT,
    on_time INTEGER,                      -- Josh-executed, Banks-tracked
    last_nudged_at TEXT
);

-- Standing job 5: opportunity engine. Never submitted, ever.
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT,
    criteria_match_score INTEGER,
    application_drafted_at TEXT,
    submitted INTEGER NOT NULL DEFAULT 0,     -- always 0 by construction; see enforcement.py
    followed_up_at TEXT,
    status TEXT NOT NULL DEFAULT 'sourced',   -- sourced | drafted | (never: submitted)
    tier TEXT NOT NULL DEFAULT 'C',           -- A | B | C
    pursuit_mode TEXT,                        -- full_time | contract_to_hire | fractional | consulting
    company_normalized TEXT,                  -- lowercase, legal suffixes stripped
    source_url TEXT,                          -- dedup primary key (exact match first)
    contact_id INTEGER,                       -- FK to contacts table
    -- 1 while comp/vertical are unknown (e.g. Simplify-only intake). Such rows
    -- are recorded but NOT surfaced to Slack — tiering is half-blind until
    -- enrichment fills comp+vertical, at which point score is recomputed and
    -- needs_enrichment flips to 0 (then Tier A/B may surface). Decision 4.
    needs_enrichment INTEGER NOT NULL DEFAULT 0,
    -- Role's industry/vertical (from JD extraction). Persisted so the warm-path
    -- referral engine can match a recruiter's vertical_fit to the role (P2).
    industry TEXT
);

-- Standing job 6: capital & research desk. Findings only.
CREATE TABLE IF NOT EXISTS capital_candidates (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    modeled_return REAL,
    hold_period_months INTEGER,
    math_shown TEXT,                          -- rendered calculation, for the memo
    professional_review_flag INTEGER NOT NULL DEFAULT 1,  -- always 1; never advice
    created_at TEXT NOT NULL
);

-- Decision Packets + Action Queue (v2 mechanics, B6).
CREATE TABLE IF NOT EXISTS decision_packets (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    decision TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    alternative TEXT,
    evidence TEXT,
    dollar_impact_cents INTEGER,
    reversible INTEGER NOT NULL DEFAULT 1,
    deadline TEXT,
    default_if_unanswered TEXT NOT NULL,
    answered_at TEXT,                     -- decision made
    completed_at TEXT,                    -- action actually done — tracked separately
    created_at TEXT NOT NULL
);

-- Relay (R-D1/R-D2/R-D3). The agent writes a send_intent (frozen payload +
-- send_channel) on draft; Approve flips it to 'approved'. Relay — the ONLY
-- holder of the outbound credential — reads approved intents and sends. It
-- never re-reads the draft (no drift). draft_ref = decision_packets.id (str).
CREATE TABLE IF NOT EXISTS send_intents (
    draft_ref TEXT PRIMARY KEY,           -- = decision_packets.id
    send_channel TEXT NOT NULL,           -- email:praise | email:sendas | none:internal
    to_addr TEXT,
    subject TEXT,
    body TEXT,                            -- frozen bytes rendered + approved (R-D2)
    status TEXT NOT NULL DEFAULT 'pending', -- pending | approved | sent | suppressed
    created_at TEXT NOT NULL
);

-- Idempotency + receipts (R-D2). UNIQUE draft_ref = send exactly once even if
-- the same Approve is seen twice. Failure leaves a row that ages in the brief.
CREATE TABLE IF NOT EXISTS sent_receipts (
    draft_ref TEXT PRIMARY KEY,           -- unique claim guard
    status TEXT NOT NULL,                 -- sending | sent | failed
    provider_id TEXT,
    error TEXT,
    updated_at TEXT NOT NULL
);

-- promises.md structured mirror — dollars-at-risk aging.
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    to_whom TEXT,
    made_at TEXT NOT NULL,
    due_at TEXT,
    dollars_at_risk_cents INTEGER,
    status TEXT NOT NULL DEFAULT 'open'   -- open | done | aged | killed
);

-- Weekly scorecard history (Part 5 8-line scorecard + extras).
CREATE TABLE IF NOT EXISTS scorecard_weekly (
    week_ending TEXT PRIMARY KEY,
    occupancy_pct REAL,
    vacancy_days INTEGER,
    inquiries_answered_under_1h_pct REAL,
    applications_from_inquiries_pct REAL,
    collections_on_time_pct REAL,
    bills_on_time_pct REAL,
    reviews_requested INTEGER,
    reviews_received INTEGER,
    money_found_cents INTEGER,
    applications_queued INTEGER,
    applications_submitted INTEGER,
    maintenance_over_7d INTEGER,
    compute_cost_cents INTEGER,
    hours_saved REAL,
    hourly_value_cents INTEGER,
    monthly_cost_cents INTEGER
);

-- Activity log (B-D4): append-only event journal. Source for ROI meter,
-- weekly scorecard hours_saved, and nightly recap. Never updated, only inserted.
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,          -- draft_created | draft_approved | draft_sent | vacancy_flagged | bill_nudged | opportunity_drafted | inquiry_answered | conflict_flagged | reflection_posted
    ref TEXT,                    -- decision_packets.id or other entity id, nullable
    minutes_saved REAL,          -- estimated time this would take Josh manually
    meta TEXT,                   -- JSON blob, arbitrary context
    ts TEXT NOT NULL             -- ISO-8601 UTC
);

-- Collections (Phase I A1): per-room rent tracking. Banks tracks & nudges only,
-- never handles money. Populated from PadSplit SourcePort once creds land;
-- seeded manually in tests. Feeds collections_on_time_pct on the scorecard.
CREATE TABLE IF NOT EXISTS rent_charges (
    id INTEGER PRIMARY KEY,
    room_id INTEGER REFERENCES rooms(id),
    period_start TEXT NOT NULL,            -- ISO date (month start)
    period_end TEXT NOT NULL,              -- ISO date (month end)
    amount_cents INTEGER NOT NULL,
    due_date TEXT NOT NULL,                -- ISO date
    status TEXT NOT NULL DEFAULT 'pending' -- pending | paid | late | waived
);

CREATE TABLE IF NOT EXISTS rent_payments (
    id INTEGER PRIMARY KEY,
    room_id INTEGER REFERENCES rooms(id),
    charge_id INTEGER REFERENCES rent_charges(id),
    paid_at TEXT NOT NULL,                 -- ISO datetime
    amount_cents INTEGER NOT NULL,
    source TEXT                            -- padsplit | manual | other
);

-- Issues (Phase I T2-7): 3 reds → Issue; every closed Issue names its artifact.
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    trigger TEXT NOT NULL,             -- '3_reds' | '3_consecutive_red_weeks' | 'manual'
    week_ending TEXT,                  -- the scorecard week that triggered it
    status TEXT NOT NULL DEFAULT 'open',  -- open | closed
    artifact TEXT,                     -- required on close: what permanent thing was made
    opened_at TEXT NOT NULL,
    closed_at TEXT
);

-- Contact discipline (Phase I T2-8): suppression list + 48h touch log.
-- Both enforced inside flow.propose() so no draft can bypass them.
CREATE TABLE IF NOT EXISTS suppression_list (
    address TEXT PRIMARY KEY,          -- email or name to never contact
    reason TEXT,
    added_at TEXT NOT NULL
);

-- Company exclusion list (MOD-06). Company-only — former employees contactable
-- if they have since moved on. Checked against opportunity.company_normalized.
CREATE TABLE IF NOT EXISTS company_exclusions (
    company_normalized TEXT PRIMARY KEY,
    reason TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS touch_log (
    id INTEGER PRIMARY KEY,
    address TEXT NOT NULL,
    draft_ref TEXT,                    -- decision_packets.id
    touched_at TEXT NOT NULL
);

-- MOD-02: 1st-degree contact graph for warm-path outreach.
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    name TEXT,
    company TEXT,
    email TEXT,
    linkedin_url TEXT,
    degree INTEGER NOT NULL DEFAULT 1,  -- 1st-degree only at launch
    source TEXT NOT NULL,               -- linkedin_csv | alumni_csv | recruiter_registry | manual
    -- Decision 5: alumni & recruiters overlap the LinkedIn connections dump, so
    -- ingestion MERGES on linkedin_url and upgrades the source label (recruiter
    -- > alumni > linkedin) rather than skipping — otherwise a warm recruiter is
    -- indistinguishable from a random connection. These hold the richer fields.
    title TEXT,                         -- recruiter/contact title
    vertical_fit TEXT,                  -- recruiter registry: GTM/PropTech/etc.
    notes TEXT,                         -- recruiter registry notes (surfaced in Slack)
    position TEXT,                      -- connection/alumni current position
    -- Contact enrichment (MOD-02): verified = provider confidence high enough to
    -- email; unverified/none routes to a LinkedIn DM draft instead. enriched_at
    -- drives the 30-day cache TTL so the same person isn't re-looked-up.
    verified INTEGER NOT NULL DEFAULT 0,
    enriched_at TEXT,
    added_at TEXT NOT NULL
);

-- Contact-enrichment queue (MOD-02). A Tier A/B opportunity with no known warm
-- contact enqueues its company here. A nightly job submits the batch to the
-- EnrichmentPort (Clay); a retrieve job writes results into contacts and
-- re-runs the warm-path attach. Batch/async by nature — Clay returns later.
CREATE TABLE IF NOT EXISTS enrichment_queue (
    id INTEGER PRIMARY KEY,
    company_normalized TEXT NOT NULL,
    role_hint TEXT,                     -- opportunity title, guides discovery
    opportunity_id INTEGER,             -- which opp to re-attach on resolve
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | submitted | done | failed
    batch_id TEXT,
    requested_at TEXT NOT NULL,
    resolved_at TEXT
);

-- Correction taxonomy (Phase I T2-9): 8-code reason on every Revise action.
-- Stored against the packet; feeds lesson quarantine (C1).
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    packet_id INTEGER REFERENCES decision_packets(id),
    code TEXT NOT NULL,                -- see CORRECTION_CODES in approval.py
    note TEXT,
    recorded_at TEXT NOT NULL
);

-- Lesson quarantine (Phase I T2-10): LOCAL → PROVISIONAL → FLEET.
-- Nothing promotes itself; promotion requires 2+ independent instances.
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    source_packet_id INTEGER REFERENCES decision_packets(id),
    stage TEXT NOT NULL DEFAULT 'local',  -- local | provisional | fleet
    instance_count INTEGER NOT NULL DEFAULT 1,
    promoted_at TEXT,
    created_at TEXT NOT NULL
);

-- Weekly biggest-miss (Phase I T2-12): one named miss per week.
CREATE TABLE IF NOT EXISTS weekly_misses (
    week_ending TEXT PRIMARY KEY,
    miss TEXT NOT NULL,
    owned_at TEXT NOT NULL
);

-- Daily Find (Phase I A3): one learning item per day. 'none' kind = honest empty.
CREATE TABLE IF NOT EXISTS daily_finds (
    date TEXT PRIMARY KEY,                 -- ISO date (the day)
    kind TEXT NOT NULL DEFAULT 'none',     -- article | fact | tip | none
    title TEXT,
    url TEXT,
    summary TEXT,
    recorded_at TEXT NOT NULL
);

-- Self-healing (B9): retry-3-then-dead-letter + labeled degradation.
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed | dead_letter | degraded
    degradation_label TEXT
);

-- Temporal memory freshness (B9): rent comps 30d, vendor quotes 90d, bills always current.
CREATE TABLE IF NOT EXISTS fact_freshness (
    fact_key TEXT PRIMARY KEY,
    fact_kind TEXT NOT NULL,              -- rent_comp | vendor_quote | bill | other
    recorded_at TEXT NOT NULL,
    value TEXT
);

-- MOD-03: Outreach lanes — one row per lane per opportunity.
-- Each lane is a separately-approvable Slack card (surround.py).
CREATE TABLE IF NOT EXISTS outreach_lanes (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL,
    lane_type TEXT NOT NULL,  -- hiring_manager | recruiter | employee | warm_intro | pov_brief | linkedin | no_role | consulting
    contact_id INTEGER,
    draft_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | sent | stalled | frozen | skipped
    created_at TEXT NOT NULL,
    sent_at TEXT
);

-- MOD-03: Warm-intro state machine (ASKED → AGREED → INTRODUCED; auto-STALLED after 7 days).
CREATE TABLE IF NOT EXISTS warm_intros (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'ASKED',  -- ASKED | AGREED | INTRODUCED | STALLED
    asked_at TEXT NOT NULL,
    state_changed_at TEXT NOT NULL,
    notes TEXT,
    UNIQUE(opportunity_id, contact_id)
);

-- MOD-04: Follow-up cadence queue (Day 3/7/14 keyed from sent_at).
-- Stops on: 'got a reply', 3 touches reached, or opportunity status in (interviewing, closed).
CREATE TABLE IF NOT EXISTS cadence_queue (
    id INTEGER PRIMARY KEY,
    outreach_lane_id INTEGER NOT NULL,
    touch_number INTEGER NOT NULL,  -- 1 | 2 | 3
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | surfaced | sent | skipped | frozen
    draft_ref TEXT,
    surfaced_at TEXT,
    sent_at TEXT,
    UNIQUE(outreach_lane_id, touch_number)
);

-- MOD-04: Governance ledger — daily channel caps (email 40/day, LinkedIn 20/day).
-- Overflow queues to next day, never dropped.
CREATE TABLE IF NOT EXISTS governance_ledger (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    channel TEXT NOT NULL,    -- email | linkedin
    count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(date, channel)
);

-- MOD-04: Collision protection — company freeze on 'got a reply' signal.
-- thaw_at NULL = manual-thaw only; ISO datetime = auto-thaws at that time.
CREATE TABLE IF NOT EXISTS company_freeze (
    company_normalized TEXT PRIMARY KEY,
    frozen_at TEXT NOT NULL,
    reason TEXT,              -- got_reply | manual
    thaw_at TEXT
);

-- MOD-04: Funnel event log (applied → contacted → replied → intro_made → interview → offer).
-- Derived from existing signals + buttons; shown as weekly scorecard funnel line.
CREATE TABLE IF NOT EXISTS funnel_events (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,  -- applied | contacted | replied | intro_made | interview | offer
    ts TEXT NOT NULL
);

-- MOD-05: per-item queue view-state (snooze/skip/aging), separate from the
-- decision/send lifecycle in decision_packets. The Daily Attack Queue renders
-- and tracks its own view-state; it does not recompute pipeline state.
-- first_surfaced_at drives aging (carried-over = active with an earlier date).
CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY,
    draft_ref TEXT,                       -- live card's DraftRef (nullable for info rows)
    category TEXT NOT NULL,               -- carried_over|active_convo|tier_a|tier_b|follow_up|relationship|imported|funnel
    opportunity_id INTEGER,
    state TEXT NOT NULL DEFAULT 'active', -- active | snoozed | skipped | done
    snooze_until TEXT,                    -- ISO date; re-include when snooze_until <= today
    first_surfaced_at TEXT NOT NULL,      -- set once (INSERT OR IGNORE) — drives aging
    last_surfaced_at TEXT NOT NULL,
    card_ts TEXT,                         -- Slack ts of the card message → revision-thread mapping
    UNIQUE(draft_ref)
);

-- MOD-05: one queue root per date — exactly-once posting under self-heal retry.
-- Same idempotency discipline as Relay sent_receipts: a duplicate fire is a no-op.
CREATE TABLE IF NOT EXISTS daily_queue (
    date TEXT PRIMARY KEY,                -- ISO date
    root_ts TEXT,                         -- Slack ts of the summary header post
    posted_at TEXT NOT NULL
);
