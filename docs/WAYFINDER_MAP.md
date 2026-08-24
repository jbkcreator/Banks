# Wayfinder Map — Banks Maximum Distribution Build

_Effort:_ Maximum Distribution Build (MOD-01 through MOD-06)
_Lead:_ Hari Krishnan (heu.ai) | _Client:_ Josh Kantor | _Deadline:_ Fri Aug 28, 2026

---

## Destination

A fully operational Banks system that maximises Josh's distribution across all job-search channels — intake, contact resolution, 7 outreach lanes, follow-up governance, Slack cockpit, and adversarial exclusion — live and in daily use by end of day Friday Aug 28, 2026.

---

## Notes

- **Domain:** Job search automation. Banks is Josh's personal AI employee, hard-walled from all Forced Action systems. Drafts only — Josh approves every touch via Slack.
- **Foundation built:** Tasks 1–10 complete (220 tests green). Core infra reusable: `relay.py`, `approval.py`, `packets.py`, `flow.py → propose()`, `enforcement.py`, `contacts.py`, `socket_listener.py`, `chatport.py`.
- **Skills to consult:** `/grill-with-docs` for spec ambiguities; `/tdd` for each module build.
- **Standing preference:** Keep SQLite (no server DB needed at this scale). All external services use Port pattern (Fake + Live).
- **Client queries:** See `CLIENT_QUERIES.md` — Josh's answers unblock each module.

---

## Decisions so far

_(Build starting — none recorded yet. Will update as decisions are made and locked.)_

---

## Not yet specified (fog of war)

### MOD-01
- Exact scoring formula for Tier A/B/C (weights per criterion) — blocked on Josh's answer
- Whether pursuit mode is LLM-classified or always manual
- Dedup merge strategy when same role appears in multiple sources

### MOD-02
- Which enrichment provider (Hunter.io vs Anymail vs other)
- Shape of LinkedIn connections CSV — need Josh's export to confirm column names
- Whether warm-path graph needs to traverse 2nd-degree connections or 1st only
- How stale data is handled (connections CSV will be months old)

### MOD-03
- LinkedIn delivery mechanism (clipboard on Josh's machine vs copy from Slack) — **architecture-deciding**
- Tone/voice spec — need example messages from Josh before LLM prompts can be written
- Warm Intro state machine persistence — whether STALLED intros resurface automatically
- POV brief length and structure

### MOD-04
- Reply-stop trigger mechanism — manual vs inbox-monitoring (inbox access not provisioned)
- Whether company collision freeze applies to warm intro lane or only direct outreach
- Exact funnel stages — spec list may need refinement

### MOD-05
- Snooze duration UX (fixed vs Josh-picks-at-snooze-time)
- NLP revision parsing approach — rule-based ("shorter", "formal") vs LLM instruction-following
- Whether lookup commands hit warm-path graph or a simpler substring search

### MOD-06
- Production host — determines deployment steps, process manager, log strategy
- Indirect exclusion depth (company only vs parent company vs portfolio companies)

---

## Out of scope

Items explicitly deferred per the build doc (`Banks_Maximum_Distribution_Build.md §3`):

- Full public ATS-board scrapers / background polling
- Direct browser automation or re-implementation of LoopCV/Simplify auto-appliers
- Advanced conversational Slack commands beyond core retrieval
- Autonomous sending / Standing Orders (every touch routes through Slack approvals)
- Complex multi-week dormant network campaign state machines
- Automated market signal monitoring (funding rounds, executive departures)

---

## Module status at map creation (2026-08-24)

| Module | Built | Sprint target |
|---|---|---|
| MOD-01: Intake, Dedup, Fit Scoring | 0% | Mon Aug 24 (today) |
| MOD-02: Contact Resolution & Warm-Path | 0% | Mon Aug 24 (today) |
| MOD-03: 7 Distribution Lanes | 0% logic / 40% infra | Wed Aug 26 |
| MOD-04: Follow-up Cadence & Ledger | ~25% | Wed Aug 26 |
| MOD-05: Slack Attack Queue | ~20% | Fri Aug 28 |
| MOD-06: Exclusion & Launch Staging | ~40% | Fri Aug 28 |
