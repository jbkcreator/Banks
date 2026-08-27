# Build Decisions — MOD-03, 04, 05, 06

_Locked from the grill sessions (2026-08-25), grounded in
`Banks_Maximum_Distribution_Build.md`. "Grilled" = decided in interview;
"plan-derived" = taken straight from the spec, no client input needed._
_Open client questions live in `CLIENT_QUERIES_V2.md` (items 6–12)._

---

## MOD-03 — Distribution Lanes & Surround

**Grilled**
- **Surround pack:** Approve on a Tier A role generates the **full applicable
  surround pack at once**; each lane is its own separately-approvable card; only
  lanes with a real target are generated (no mutual → no warm-intro card; no
  verified email → that person becomes a LinkedIn card).
- **Draft content:** facts come **only** from `career-facts.md`; voice from Josh's
  tone examples. Empty career-facts → Banks **refuses and asks**, never invents
  (constitution's no-embellishment rule).
- **Warm-Introduction state machine:** `warm_intros` table; **ASKED** on approve;
  Josh advances via Slack buttons (They agreed → **AGREED**, Intro made →
  **INTRODUCED**); **auto-STALLED after 7 days** of no movement, resurfaced in the
  brief. All transitions manual — Banks never assumes a human reply.
- **POV brief:** Tier A only; one extra card in the pack; built from JD +
  career-facts + LLM reasoning; labelled "draft POV — verify specifics"; no
  external scraping (plan §3).
- **Network Activation Lite:** daily job surfaces ~3 contacts untouched 14+ days
  (respects the governance ledger), ranked decision-makers / recruiters /
  target-vertical first. _(Daily count is client question #9.)_
- **No-Open-Role & Consulting/Fractional lanes:** **Josh-initiated** — he names a
  company + angle, Banks drafts. Plus one auto-hook: any opportunity classified
  `pursuit_mode = fractional/consulting` routes to the consulting lane. No
  auto-discovery (scrapers/market-signals out of scope). _(Client questions #6/#7.)_
- **LinkedIn-lane cards:** profile deep-link + copy-ready draft text + a
  **"Mark sent"** button (Banks can't send on LinkedIn; Approve = "I sent it,"
  feeding cadence + funnel). No clipboard automation (server-hosted).

**Plan-derived (no grill)**
- Hiring Manager Lane: personalised email + LinkedIn connection note + follow-ups.
- Recruiter Lane: standing "keep me on file for GTM mandates" note. _(Framing is
  client question #8.)_
- Employee / Referral Lane: soft networking notes to connections at the company.
- Email delivery: `send_channel = email:sendas` → Relay sends via Resend on
  approve (activates when domain + from-email land, CLIENT_QUERIES_V2 #4).

---

## MOD-04 — Follow-up Cadence, Governance & Collision Ledger

**Grilled**
- **Cadence:** Day **3 / 7 / 14** (per signed plan) keyed off the **sent**
  timestamp (Relay send or manual "Mark sent"). Stops on: manual **"Got a reply"**
  button, OR 3 touches reached, OR opportunity → interviewing/closed.
- **Collision protection:** the manual "Got a reply" signal also **freezes other
  pending outreach at that company**; frozen touches show in the brief; on a
  7-day stall Banks recommends a **single** secondary escalation — never a blast.
- **Governance ledger:** caps outreach **surfaced per channel per day**
  (email 40, LinkedIn 20); overflow **queues to the next day**, never dropped.
  14-day per-contact spacing already enforced in `flow.propose()` (touch log).
  _(LinkedIn cap governs Banks' output, not Josh's manual clicks.)_
- **Funnel / results tracking:** derived from existing signals + buttons —
  applied = opportunities; contacted = opportunity with a contact attached;
  replied = "Got a reply" taps; intros = warm-intro INTRODUCED; interviews/paid =
  one new **"Interview/Offer"** button. Shown as a weekly-scorecard funnel line.

---

## MOD-05 — Slack Command & Control / Daily Attack Queue

_Grilled 2026-08-27 (27 decisions). MOD-05 is a **view + routing layer** over the
MOD-01/03/04 pipeline — it renders and tracks its own view-state, it does not
recompute pipeline state._

**Surface (Q1, Q2, Q8, Q14, Q20, Q25, Q26)**
- **Channel:** own channel **`#banks-jobs`** (per the plan), separate from the
  property-ops morning brief — the two surfaces share nothing and have distinct
  cadences.
- **Layout:** one **summary header post + individually-approvable cards threaded
  beneath it** (avoids channel-flood; each draft stays separately approvable).
- **Cadence:** new `daily_attack_queue` job on the existing `scheduler.py` /
  `jobs.py`, fires **7:30 ET** (reuses self-heal retry + `job_runs`). _(TZ is
  client question — default ET.)_
- **Idempotency:** one queue **root per date** (`daily_queue` marker: date → root
  `ts`), written in the same transaction as the post-intent — posts **exactly
  once** even under self-heal retry (same discipline as Relay `sent_receipts`).
- **Fresh post per day:** never edits yesterday's post; carried-over items are
  re-posted live under today's root, yesterday's card frozen as history.
- **Order (failure-mode-first):** carried-over/aging → active conversations →
  Tier A surround → Tier B → follow-ups due → relationship outreach → imported
  digest → funnel footer. Score-ranked within each category (scores exist from
  MOD-01). Empty sections **omitted**; always posts (honest-empty + feed-nudge on
  a zero day).

**Categories (Q9, Q15, Q17, Q24)**
- **Imported applications:** informational **digest receipt** (counts by tier +
  enrichment-held note), NOT cards — actionable Tier A/B appear once in the
  surround section; Tier C log-only.
- **Active conversations:** the **frozen-company view** driven by the MOD-04
  "Got a reply" button (`got_reply()`) — **no inbound mailbox reading** (out of
  scope now; when the mailbox lands it can auto-fire `got_reply()`, section
  unchanged).
- **Relationship outreach + on-demand "call list":** one engine —
  `network_activation_due()` (MOD-03/04) rendered in both surfaces.
- **Funnel:** one-line **daily footer** via `weekly_funnel_summary()`;
  Interview/Offer recorded via on-demand mark → `record_interview()` /
  `record_offer()`. No standalone weekly post (would be scope creep).

**Actions (Q3, Q4, Q10, Q13, Q19)**
- **Reject** = terminal (drop + suppress intent). **Snooze** = time-based, default
  **1 day** (reappears next-morning; _client question_). **Skip** = today-only,
  no auto-resurface.
- **Manual LinkedIn/Call/Text** = "**Mark done**" button reusing the MARK_SENT
  path (writes `touch_log` + funnel event + stamps `outreach_lanes.sent_at`).
- **Carry-over:** untouched items reappear next day, **aging-flagged**,
  failure-mode-first. Only Snooze/Skip/Reject/action removes an item.
- **Single-approver lock:** only Josh's Slack user id
  (`BANKS_APPROVER_USER_ID`) drives actions; others get a silent no-op /
  ephemeral note. (This is why `users:read` is scoped.)
- **State store:** new `queue_items` table (draft_ref, category, state,
  snooze_until, first_surfaced_at, last_surfaced_at) — view-state kept separate
  from the decision/send lifecycle.

**Revisions & commands (Q5, Q6, Q11, Q16, Q21, Q22, Q23)**
- **Threaded NL revisions** ("shorter" / "less formal" / "stronger hook") update
  the card **in place** via `redraft()` (same packet, re-frozen intent — no
  drift). Fires only when (a) reply is in a **still-pending** Banks draft thread
  (parent `ts` → `draft_ref`) AND (b) the router classifies revise-intent;
  otherwise silent.
- **No-embellishment guard extends to revisions:** the rewrite prompt gets
  career-facts as the **only** fact source ("rephrase tone/length/structure; do
  NOT add any fact, number, title, or claim not present"), plus a post-rewrite
  check that **flags** any new number/entity rather than posting. "Stronger hook"
  cannot become embellishment.
- **Hybrid intent router (researched 2026-08-27):** Layer 1 keyword fast-path
  (handles the common phrasings, zero LLM cost, works if the key is down) →
  Layer 2 LLM `extract_json` fallback with a small intent enum (absorbs typos /
  phrasing). LLM never sees a large tool list, staying in the high-accuracy
  regime. Stays inside the plan's "core retrieval actions" — parses intent, does
  not become an open-ended conversational agent (explicitly out of scope).
- **Commands:** `who do I know at <company>` → `warmpath.find_referral_paths`;
  `status <company>` → **pipeline snapshot** (tier + pursuit mode, lane states,
  warm-intro state, cadence position, freeze status, known contacts — pure read);
  `call list` → `network_activation_due()`.
- **Listener precedence:** (1) halt/kill check **always first** (a shadowed kill
  switch is broken) → (2) thread-scoped revise → (3) channel-scoped command →
  (4) ignore.
- **Cost guard:** keyword short-circuit + light per-user debounce and a rolling
  LLM-call cap ("give me a sec" past the ceiling). Cheap insurance, not a quota
  system.

**Transport & testing (Q12, Q18)**
- **Transport-agnostic core:** clicks and reactions both route through the same
  `apply_action` core. **Socket Mode + buttons primary; emoji-reaction poller a
  first-class fallback** so launch isn't blocked on the app. NL revisions
  unavailable in fallback mode (reactions can't carry "shorter").
- **Testing:** split into pure `attack_queue.build_sections(db_path, now) ->
  [Section]` (fully unit-testable — carry-over, aging, omit, digest, one-engine
  call-list) + a thin render layer over `FakeChatPort` / `LiveChatPort`. Router
  tested with `FakeLLMPort`. **CI is Fake-port-only, zero network**; a live smoke
  test runs in Lesly's personal test workspace (full Socket Mode — buttons + NL
  revisions — since org policy doesn't restrict her own workspace).

**Graceful degrade (Q27)**
- Empty `career-facts.md` (the current real state) does **not** crash the queue:
  blocked sections show a single "fill career-facts to unlock drafts" line
  instead of cards. Self-surfacing resume nag from the cockpit.

**Open infra (production only, not a build blocker):** live approval buttons in
**Josh's** workspace need a **dedicated Banks Slack app** with Socket Mode (the
FA "Cora Approvals" tokens must not be reused — hard wall + event collision). The
app manifest is `docs/launch/banks_slack_app_manifest.yaml`. Building and proving MOD-05
does **not** depend on this — it's done live in Lesly's test workspace; the app
is the production cutover. Tracked in CLIENT_QUERIES_V2 #2.

**Client questions surfaced (both non-blocking; defaults built):**
1. **Snooze duration** — next-morning default, or pick the delay each time?
2. **Timezone** — is 7:30 **ET** Josh's morning?

---

## MOD-06 — Adversarial Exclusion & Launch Staging

_Grilled 2026-08-27 (13 decisions, Q1-Q13). MOD-06 hardens the exclusion wall
and stages launch. It adds a second (send-time) gate, person/indirect
exclusion, an adversarial suite, a mock + live E2E, and the launch runbook._

**Exclusion enforcement (Q1, Q2, Q3, Q8, Q10)**
- **Two gates:** block at **draft creation AND send time**. Draft-time =
  intake (`is_company_excluded`) + surround/lane assembly (person + indirect).
  Send-time = a NEW re-check in Relay right before it sends an approved intent —
  catches anything excluded *after* it was queued (the client's worst case:
  "approved but shouldn't have sent").
- **Person exclusion:** keyed on a **stable identity — LinkedIn URL first, then
  normalized name** — not the raw email string (people change jobs/emails).
  New `person_exclusions` concept, distinct from raw-address `suppression_list`.
- **Indirect (Q3, _client question #11_):** default blocks the excluded firm +
  its **current employees as intro-conduits** + any company whose normalized
  name **contains the excluded slug** (Rent Solutions Holdings/LLC/Group). Does
  NOT auto-block ex-employees or unrelated affiliates — those need Josh's named
  list. Negative control: a former employee who has **moved on** stays
  contactable (must not over-block).
- **Gate placement:** person + indirect enforced inside
  `surround.generate_surround_pack` / lane drafters — filter every candidate
  contact and intro conduit before a lane is built; excluded contact → lane
  skipped; conduit at an excluded firm → path dropped. Relay = send-time backstop.
- **Observability:** **visible, terse block reasons at both gates** — intake/
  queue line at draft-time ("🚫 Skipped 1 — excluded (Rent Solutions)"), card
  note at send-time ("🚫 Not sent — target now excluded"). No silent drops
  (silent blocking hides bugs + over-blocking).

**Exclusion list management (Q9, Q11)**
- **File-as-source-of-truth**, seeded at startup + reloadable (reviewable,
  version-controllable), with an optional Slack `exclude <company/person>`
  command that writes back to the same file. **Removal is deliberate** (edit
  file / `unexclude`) — no accidental tap un-blocks a competitor.
- **List contents (_client question #12_):** ship with **Rent Solutions** as the
  only seeded default; the full list (former employers, competitors, named
  individuals, conflicts) is a **hard launch dependency from Josh** — gates the
  live-E2E sign-off. Cannot be inferred.

**Testing (Q4, Q5)**
- **Adversarial suite** = evasion matrix at BOTH gates: (1) company casing/
  suffix/whitespace variants; (2) excluded person via a different email/company
  after a job move; (3) indirect — warm-intro routed via an employee at the
  excluded firm; (4) race — draft created pre-exclusion, approved post-exclusion;
  (5) corporate-substring variant; (6) negative control — moved-on ex-employee
  still contactable.
- **Mock E2E** = one deterministic **all-Fakes pytest** (FakeCSV / FakeLLM /
  FakeEnrichment / FakeChat), intake → enrichment → scoring → surround → Slack
  approval round-trip, with a **planted exclusion** that never surfaces or sends.
  CI-green forever, zero network.

**Launch staging (Q6, Q7, Q12, Q13)**
- **Live E2E = mandatory, run LAST** as the launch acceptance gate. Pass = a
  **7-item signed checklist** (`docs/launch/LAUNCH_ACCEPTANCE.md`): (1) real job → Tier
  A/B card in the real channel; (2) real hiring manager; (3) verified email or
  LinkedIn fallback; (4) real 1st/2nd-degree warm path; (5) full surround pack on
  Approve + Day 3/7/14 follow-up scheduled; (6) planted exclusion never surfaces
  + blocked at send; (7) nothing sends without Josh's tap. Josh signs all 7.
- **Migration (Q7): DONE** — repo connected at github.com/jbkcreator/Banks.
  Residual cheap insurance: a **secrets-in-history scan** across all commits
  (`.env`/`banks_live.db`/`.secrets/` were git-ignored — confirm nothing leaked
  in an early commit).
- **`docs/launch/LAUNCH_RUNBOOK.md`** — ordered go-live: (1) merge stack bottom-up
  (mod01-02 → 03-04 → 05 → 06), green CI each step; (2) adversarial suite + mock
  E2E green on main; (3) secrets-in-history scan clean; (4) provision Josh's prod
  Slack app from the manifest + prod `.env` (tokens, channel,
  BANKS_APPROVER_USER_ID, timezone); (5) load exclusion list + populate
  `career-facts.md`; (6) live-E2E acceptance sign-off; (7) turn on the scheduler
  → live; (8) **rollback/halt** section.
- **Halt = real global freeze (Q13):** the kill command must genuinely make the
  scheduler skip runs AND Relay refuse sends while halted (checked at both
  points), not just post "halted" to Slack. Verified by a test asserting a
  halted Banks neither fires the scheduled job nor sends an approved intent.

**New build work MOD-06 implies (beyond existing company-at-intake gate):**
Relay send-time exclusion+halt gate · `person_exclusions` (LinkedIn/name key) +
indirect/conduit filtering in surround · exclusion seed-file loader (+ optional
Slack command) · adversarial suite + mock-E2E pytest · `LAUNCH_ACCEPTANCE.md` +
`LAUNCH_RUNBOOK.md` · verify/wire halt to freeze scheduler + Relay.

---

## Cross-cutting build dependencies (existing client blockers)
- `career-facts.md` (resume) → real MOD-03 draft **content**
- domain + from-email → MOD-03 email **sending** (Relay/Resend)
- dedicated Banks Slack app → live MOD-05 **approval buttons**
- exclusion depth + full list → complete MOD-06 **enforcement**

All modules are buildable now with Fakes (as MOD-01/02 were); live content /
sending / buttons activate as each blocker clears.
