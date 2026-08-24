# Banks — Client Queries for Josh Kantor
_Last updated: 2026-08-24 | Deadline: Friday Aug 28, 2026_

## How to use this document
These are the questions and items we need from you before or during each module build. Answer the ones marked **🔴 Blocking** first — the build cannot start that module without them. Items marked **🟡 Soon** are needed within 1–2 days of the module starting.

---

## MOD-01: Application Intake, Dedup & Fit Scoring

### Decisions Josh needs to make
- 🔴 **Tier A/B/C thresholds** — What criteria make a role Tier A (Full Surround) vs B (Targeted) vs C (Monitor only)? E.g. does title match, comp range, remote policy, company size all factor in? Or is it purely a gut-feel manual tag?
- 🔴 **Pursuit mode rules** — How does Banks decide if an opportunity is Full-Time, Contract-to-Hire, Fractional, or Consulting? Is it based on the job posting text, or does Josh always classify manually?
- 🟡 **Duplicate definition** — If the same role appears in LoopCV and a manually entered URL, how should Banks merge them? Keep first entry? Merge fields?
- 🟡 **Scoring weights** — The DB has a `criteria_match_score` (0–100). What factors should drive that score? (e.g. title 40%, comp 30%, remote 20%, industry 10%)
- 🟡 **Company name normalisation** — Should "AppFolio Inc", "Appfolio", and "APPFOLIO" all deduplicate to one record? Any edge cases (subsidiaries, parent companies)?

### Credentials / access Josh needs to provide
- 🔴 **LoopCV export format** — Share a sample CSV export from LoopCV so we can build the parser against the real column names.
- 🔴 **Simplify export format** — Share a sample CSV/export from Simplify (or confirm it uses the same format as LoopCV).
- 🟡 **Anthropic API key** — Needed for LLM-based fit scoring and classification. Set as `ANTHROPIC_API_KEY` env var.

### Data Josh needs to export or share
- 🟡 **Existing application history** — If Josh has any existing applied roles in a spreadsheet or ATS, share it so we can seed the DB and test dedup logic against real data.

---

## MOD-02: Contact Resolution, Enrichment & Warm-Path Graph

### Decisions Josh needs to make
- 🔴 **Enrichment provider** — Do you have a Hunter.io account, Anymail Finder, or another email enrichment service? Or should we sign up fresh? (Hunter.io free tier: 25 searches/month; paid starts at $49/mo.)
- 🔴 **LinkedIn connection graph** — Are you comfortable exporting your LinkedIn 1st-degree connections CSV? (Settings → Data Privacy → Get a copy of your data → Connections.) This is the warm-path graph source.
- 🟡 **Exec recruiter registry** — Do you have an existing list of GTM/PropTech/SaaS recruiters you work with? Even a rough spreadsheet is fine — we'll import it.
- 🟡 **Fallback behaviour** — If email enrichment fails (contact not found), should Banks: (a) skip the contact and flag it, (b) fall back to LinkedIn DM only, or (c) prompt Josh to find it manually?

### Credentials / access Josh needs to provide
- 🔴 **Hunter.io API key** (or Anymail Finder key) — Set as `BANKS_HUNTER_API_KEY` or `BANKS_ANYMAIL_KEY`.
- 🟡 **LinkedIn connections export** — A `.csv` file (exported from LinkedIn settings). One-time import; re-export monthly for freshness.

### Data Josh needs to export or share
- 🟡 **Alumni / former colleagues list** — Any list of people Josh has worked with previously that he'd want as warm-path nodes (name + company + LinkedIn URL is enough).
- 🟡 **Executive recruiter list** — Names, firms, emails/LinkedIn of recruiters Josh already has relationships with.

---

## MOD-03: 7 Distribution Lanes & Surround Workflow

### Decisions Josh needs to make
- 🔴 **Tone & voice** — What's the writing style for outbound messages? (e.g. direct/executive, warm/collegial, formal/buttoned-up?) Share 2–3 examples of outreach Josh has sent that he's happy with — we'll use these as style anchors for the LLM drafts.
- 🔴 **LinkedIn clipboard delivery** — Banks generates LinkedIn message drafts and connection notes. How will Josh use them? (a) Banks runs on Josh's own laptop and copies to clipboard directly, (b) Banks runs on a server and Josh copies from the Slack draft, (c) Banks emails the draft. This changes the architecture of the LinkedIn lane.
- 🟡 **Warm Intro request wording** — When asking a mutual connection for an intro, what's the preferred framing? (e.g. "Would you be open to making a quick intro?" vs "Happy to make it easy — here's a blurb you can forward.") Share an example if you have one.
- 🟡 **Proof-of-value brief scope** — For Tier A targets, Banks generates a 30/60/90-day POV. How long should these be? (Half a page? One page?) Should they always include a GTM observation for the target company?
- 🟡 **No-Open-Role Lite targets** — Which companies does Josh want to approach even without an active posting? A list of 5–10 target companies to seed this lane.
- 🟡 **Consulting/Fractional Lite targets** — Same question — which companies or problem areas should Banks target with a fractional/consulting pitch?

### Credentials / access Josh needs to provide
- 🔴 **Resend API key** — For email lanes (HM lane, Recruiter lane). Set as `BANKS_RESEND_API_KEY`. Already referenced in relay.py but key not yet provisioned.
- 🔴 **Sending email address** — What email address should outbound messages come from? (Josh's personal, a custom domain, or a dedicated outreach address?)

### Data Josh needs to export or share
- 🟡 **2–3 example outreach messages** — Past emails or LinkedIn notes Josh has sent and liked. Used to train the LLM tone.
- 🟡 **Target company list** — For No-Open-Role and Consulting/Fractional lanes.

---

## MOD-04: Follow-up Cadence, Reply-Stop & Ledger

### Decisions Josh needs to make
- 🔴 **Reply-stop trigger** — What counts as a "reply" that stops follow-up? (a) Any inbound email from that domain, (b) only emails explicitly referencing the application, (c) Josh manually marks it. Affects whether we need email inbox monitoring.
- 🟡 **Rate cap confirmation** — The spec says 20 LinkedIn invites/day and 40 emails/day. Are these correct? Should caps reset at midnight Eastern?
- 🟡 **Company collision rule** — If Josh has an active conversation at Company A, should ALL outreach to Company A freeze (including warm intros, employee networking)? Or only direct HM/recruiter messages?
- 🟡 **Follow-up tone escalation** — Should Day 7 and Day 14 follow-ups be different in tone from Day 3? (e.g. Day 3 = gentle nudge, Day 14 = final check-in with a pivot offer.)
- 🟡 **Funnel stage definitions** — The spec lists: Applications → Contacts → Replies → Introductions → Conversations → Interviews → Paid Work. Are these the right stages? Any missing (e.g. "Phone Screen", "Offer")?

### Credentials / access Josh needs to provide
- 🟡 **Email inbox access (for reply-stop)** — If we auto-detect replies, Banks needs read-only access to Josh's inbox (Gmail OAuth or IMAP). Confirm whether this is in scope or if Josh prefers manual reply-stop.

---

## MOD-05: Slack Command & Control / Daily Attack Queue

### Decisions Josh needs to make
- 🔴 **#banks-jobs channel** — Does this channel exist in Josh's Slack workspace? If not, Josh needs to create it and invite the Banks bot. (Current bot is in #banks — this is a separate channel.)
- 🔴 **Attack Queue time** — What time should the Daily Attack Queue post each morning? (Current morning brief posts at 07:30 Eastern — same time, or different?)
- 🟡 **Snooze duration** — When Josh hits Snooze on an action, how long should it snooze? (1 day? 3 days? Josh picks at snooze time?)
- 🟡 **Skip behaviour** — When Josh skips an action, is it gone forever or does it resurface after X days?
- 🟡 **"Who do I know at X" lookup** — Should this search: (a) only Josh's LinkedIn connections CSV, (b) the full warm-path graph including alumni, (c) both?

### Credentials / access Josh needs to provide
- 🔴 **Slack bot token** (`BANKS_SLACK_BOT_TOKEN`) — Already needed for existing #banks channel. Confirm the same bot token covers #banks-jobs, or if a new Slack app scope is needed.
- 🔴 **Slack channel ID for #banks-jobs** — Set as a new env var (e.g. `BANKS_JOBS_CHANNEL_ID`). Josh needs to share this after creating the channel.

---

## MOD-06: Adversarial Exclusion & Launch Staging

### Decisions Josh needs to make
- 🔴 **Exclusion list** — Who is on the do-not-contact list? This includes: (a) individuals, (b) companies, (c) anyone connected to an excluded firm. Share as a list (name + reason + company).
- 🟡 **Indirect exclusion depth** — If Company X is excluded, should Banks also block outreach to employees of Company X's parent company? Or only direct employees?
- 🟡 **Production server** — Where will Banks run in production? (Josh's machine, a VPS, a cloud VM?) This affects the `banks.db` path, process management, and log access.

### Credentials / access Josh needs to provide
- 🟡 **Production server access** — SSH key or deployment method for the production host. Needed for MOD-06 deployment step.

### Data Josh needs to export or share
- 🔴 **Exclusion list document** — Even a rough list: "Don't contact anyone at Acme Corp, don't contact John Smith." We'll formalise the schema.

---

## Standing items (needed across all modules)

These affect the whole system and should be resolved first.

| Item | Env var | Urgency | What it unlocks |
|---|---|---|---|
| Anthropic API key | `ANTHROPIC_API_KEY` | 🔴 Blocking | LLM drafting, fit scoring, classification |
| Slack bot token | `BANKS_SLACK_BOT_TOKEN` | 🔴 Blocking | All Slack output (#banks + #banks-jobs) |
| Slack app token (Socket Mode) | `BANKS_SLACK_APP_TOKEN` | 🔴 Blocking | Button click handling |
| #banks-jobs Slack channel ID | `BANKS_JOBS_CHANNEL_ID` | 🔴 Blocking | MOD-05 Attack Queue |
| Resend API key | `BANKS_RESEND_API_KEY` | 🔴 Blocking | All email lanes (MOD-03, MOD-04) |
| Sending email address | `BANKS_FROM_EMAIL` | 🔴 Blocking | All outbound email |
| Hunter.io or Anymail key | `BANKS_HUNTER_API_KEY` | 🟡 Soon | MOD-02 contact enrichment |
| LinkedIn connections CSV | — (one-time import) | 🟡 Soon | MOD-02 warm-path graph |
| Josh's email address | `BANKS_JOSH_EMAIL` | already configured | Financial draft routing |
| Production server details | — | 🟡 Soon | MOD-06 deployment |
