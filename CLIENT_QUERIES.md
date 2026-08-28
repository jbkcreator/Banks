# Banks — Client Queries for Josh Kantor

_Last updated: 2026-08-25 | Deadline: Friday Aug 28, 2026_
_Status: **All 17 questions answered** (2026-08-25). Remaining gaps are credentials/access, tracked below._

## How to use this document
Questions are grouped by module. Each shows Josh's answer and how it maps to the
build. The **Still Outstanding** section at the top is the only thing blocking
further live testing.

---

## ⛔ Still Outstanding (blocking live/production, not yet resolved)

| Item | Env var | Needed for | Owner | Notes |
|---|---|---|---|---|
| **Anthropic API key** | `ANTHROPIC_API_KEY` / `BANKS_ANTHROPIC_API_KEY` | Real LLM — JD industry extraction, all draft copy | **Josh** | **Not mentioned in Josh's answers.** Without it Banks uses canned fake responses. Real blocker for intelligence. |
| **Banks Slack app in Forced Action Leads workspace** | `BANKS_SLACK_BOT_TOKEN` (that workspace) | Posting to real `#banks-jobs` | Josh / you | Channel `C0BNGMYHFEF` confirmed, but our bot token is for the *test* workspace. See "What we need in Slack" below. |
| **Sending email address** | `BANKS_FROM_EMAIL` | Outbound email lanes | Josh | Personal address, custom domain, or dedicated outreach address? |
| **Clay follow-up** | `BANKS_CLAY_API_KEY` | Contact enrichment | you / Josh | Josh chose Clay, but **Clay's free tier blocks all API/webhook/Sheets access** (verified live — endpoint returns "deprecated"; paid ≥ $134/mo). Decision needed: (a) manual CSV round-trip on free tier, (b) revert to plan's Hunter.io/Anymail (real APIs, cheaper), or (c) upgrade Clay. Recommend (a)+(b). |

**Not blocking now:**
- **Hetzner production server** — deferred by agreement; only needed for 24/7 deployment (MOD-06), not for build or live testing.
- **LoopCV export (Q1)** — Josh may set up LoopCV later; will confirm. Banks runs Simplify-only at launch, LoopCV slot dormant.

---

## What we need in the Slack workspace

To post to the real `#banks-jobs` in the Forced Action Leads workspace:

1. **Confirm the channel** — `#banks-jobs` exists, ID `C0BNGMYHFEF`. ✅
2. **Install the Banks Slack app into the Forced Action Leads workspace** (OAuth install). This is what produces a *workspace-specific* bot token — the channel ID alone is not enough. Our current token is for the "bank test" workspace and returns `channel_not_found` for `C0BNGMYHFEF`.
3. **Bot scopes needed:** `chat:write` (post drafts), `chat:write.public` (or invite the bot to the channel), `commands` (slash commands for manual intake / lookups), and — for the Socket-Mode button listener — an app-level token (`xapp-…`) with `connections:write`.
4. **Invite the Banks bot to `#banks-jobs`.**
5. **Hand back:** the workspace bot token (`xoxb-…`) → set as `BANKS_SLACK_BOT_TOKEN`, and confirm `BANKS_JOBS_CHANNEL_ID=C0BNGMYHFEF`.

Until then, live testing runs against the **test workspace** ("bank test", channel `C0BN4GKHJCS`), which is fully functional.

---

## Standing credentials — status

| Item | Env var | Status |
|---|---|---|
| Anthropic API key | `BANKS_ANTHROPIC_API_KEY` | ⛔ Outstanding (see above) |
| Slack bot token (real workspace) | `BANKS_SLACK_BOT_TOKEN` | ⛔ Test workspace only |
| Slack app token (Socket Mode) | `BANKS_SLACK_APP_TOKEN` | ✅ Test workspace |
| Resend API key | `BANKS_RESEND_API_KEY` | ✅ Test/sandbox key |
| Sending email address | `BANKS_FROM_EMAIL` | ⛔ Outstanding |
| Clay API key | `BANKS_CLAY_API_KEY` | ⚠️ Provided but free tier unusable (see Q7) |
| LinkedIn connections CSV | — | ✅ Received (1,694 connections) |
| Production server (Hetzner) | — | 🕒 Deferred (not blocking) |

---

## MOD-01: Application Intake, Dedup & Fit Scoring

**Q1 — LoopCV export** → _Not set up yet; Josh may set it up, will confirm._ Simplify-only at launch; LoopCV slot dormant.

**Q2 — Simplify export** → ✅ _Received_ (`Simplify_Tracked_Jobs_2026-08-24.csv`, 44 rows). Parser columns confirmed and built.

**Q3 — Application confirmation emails** → ✅ _Sample emails sent._ Confirmation parser built (`emailport.py`). Reply-detection is manual (see Q13), so no mailbox credentials needed at launch.

**Q4 — Tier A/B/C criteria** → ✅ _Now includes AE and Account Manager roles at every tier_, not just Director+. Thresholds: A ≥ 75, B 50–74, C < 50 (Josh can override per role).

**Q5 — Fit-score weighting** → ✅ _Confirmed final:_ Comp & Tier **35%** / Vertical-Network Fit **25%** / Remote-Geo Fit **20%** / Pursuit-Mode Alignment **20%**. Comp floor **$150k** base, sweet spot **$220k+**. Built in `score.py`.

**Q6 — Pursuit-mode rules** → ✅ _Propose-then-confirm in Slack_, not auto-decide. Banks proposes, Josh confirms/overrides. Modes: Full-Time / Contract-to-Hire / Fractional / Consulting.

_(Dedup behaviour: flag + propose, not auto-consolidate — confirmed.)_

---

## MOD-02: Contact Resolution, Enrichment & Warm-Path Graph

**Q7 — Enrichment provider (Clay vs Hunter/Anymail)** → ⚠️ Josh chose **Clay** ("already on FA's free tier, $0/mo"). **Problem discovered:** Clay's free tier blocks API/webhook/Google-Sheets/own-key access entirely (verified live; paid ≥ $134/mo). See Still Outstanding for the decision needed.

**Q8 — Contact discovery** → ✅ _Same human-in-the-loop logic as Q6/Q7._ Banks proposes contacts, Josh confirms.

**Q9 — Alumni contact list** → ✅ _Received_ (`Banks_Alumni_FormerColleagues.csv`, 64 former colleagues: EDGE/Rent Solutions, Lima One, AmeriLife, Ballast Point, Hyde Park Capital, HealthPlan Services).

**Q10 — Exec recruiter registry** → ✅ _Received_ (`Banks_Recruiter_Registry.csv`, 9 recruiters incl. Tabitha Francis/LMRE, covering GTM/PropTech/SaaS/Fintech).

_(Note: all 64 alumni and all recruiters overlap the 1,694 LinkedIn connections — ingestion merges them and upgrades the source label so warm/recruiter identity is preserved.)_

---

## MOD-03: 7 Distribution Lanes & Surround Workflow

**Q11 — LinkedIn delivery method** → ✅ _Confirmed: server-hosted; Josh copies draft from Slack and pastes into LinkedIn manually._ (Human-safe handoff, zero browser automation.)

**Q12 — Example outreach messages** → ✅ _3 examples provided_ (LinkedIn cold intro after applying; email warm-network ask; email relationship/gratitude note). Common thread: direct + specific ask (or explicit "no ask"), lead with a concrete credential, casual personal opener, no corporate-speak. Feeds the LLM tone prompts.

---

## MOD-04: Follow-up Cadence & Reply-Stop

**Q13 — Reply-stop method** → ✅ _Manual — Slack "got a reply" button_, not auto-mailbox, to start. No Gmail/IMAP credentials needed. Cadence: **Day 3 / 7 / 14** (per signed plan), 3 touches max.

---

## MOD-05: Slack Command & Control

**Q14 — #banks-jobs channel & workspace** → ✅ _Existing Forced Action Leads workspace, this channel._ Channel ID `C0BNGMYHFEF`. (App still needs installing in that workspace — see "What we need in Slack".)

---

## MOD-06: Adversarial Exclusion & Launch Staging

**Q15 — Exclusion list** → ✅ _Rent Solutions added._ Company-only exclusion (former Rent Solutions colleagues who moved on are still contactable). Built + seeded in `company_exclusions`.
