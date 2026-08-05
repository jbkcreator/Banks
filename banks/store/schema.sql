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
    status TEXT NOT NULL DEFAULT 'sourced'    -- sourced | drafted | (never: submitted)
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
