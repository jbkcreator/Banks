# Banks — Task Report

**Date: 05-08-2026**

## Overview

**Banks is in active development and not yet live.** By design, we are building and proving the full assistant against realistic stand-in data *ahead of* your real domain, accounts, and data being connected — so that the moment those arrive, we connect them rather than start building. To date the logic of every feature is built and validated by an automated test suite (128 tests, all passing); what remains for each is the live connection to your real services and, in a few cases, a specific input from you.

Banks prepares and proposes but never sends, posts, pays, or submits on its own, and is kept entirely separate from Forced Action — no shared systems, credentials, or data.

The codebase is currently maintained locally on our side and version-controlled here. It will move onto your private GitHub repository once that access is provisioned; until then nothing is lost and the handover will be straightforward.

The report below groups the work into tasks. Each task records its status honestly — **what is built and tested**, and **what is still pending** before it runs against your real accounts. "Built and tested" means the logic is complete and covered by automated tests using stand-in data; "pending" means a live connection or an input from you is still required.

### Status at a glance

Assessed critically. Because Banks is pre-launch and no real account or data is connected yet, **no feature is fully live** — each awaits at least one connection or input. The honest split is therefore between work that is build-complete-and-tested but not yet connected (**In progress**), and work not yet begun (**Not started**).

| Task | Category | What stands between it and live |
|------|----------|--------------------------------|
| 1 — Activity Log & ROI | In progress | Operating-cost figure + live events flowing from other tasks |
| 2 — Daily Command Centre | In progress | Your Slack workspace; the send path also needs the domain + sender |
| 3 — Email-Reading Layer | In progress | The AI key; live adapter not yet run against the real service |
| 4 — Bill Pipeline | In progress | The mailbox (to receive) and the live AI key |
| 5 — Receipt Filing | In progress | Production Google authorization, per-property folder IDs, live AI key |
| 6 — Opportunity Pipeline | In progress | Your resume (v14) and the live AI key |
| 7 — Message Classification & Surfacing | In progress | The live AI key and real inbound channels |
| 8 — Rental Operations Refinements | In progress | Live PadSplit access (read-only) |
| 9 — Calendar, Market Brief & Container | In progress | Production Google authorization for the calendar |
| 10 — Capital Deployment Modelling | **Not started** | Deliberately held until custodian confirmation + legal review |

None are in a "completed, ready to go live" state today; every "In progress" item is build-complete and tested, and needs only the connection or input noted above — not further engineering.

---

## Task 1 — Activity Log and ROI Meter

**Status:** In progress — logic built and tested; pending your operating-cost figure

**What We Built**

An append-only activity journal that records every action Banks takes along with the time it saves you, and a weekly ROI meter that values that time at your stated $48/hour and nets it against operating cost. The journal is the single source the morning brief, scorecard, and nightly recap all read from.

**Components**

The journal lives in `banks/activity_log.py` — a `log_event()` writer and a `hours_saved_this_week()` reader, backed by an `activity_log` table added to the store schema. The ROI valuation lives in `banks/schedule.py`, which fixes the hourly value at the conservative $48/hour figure and computes the weekly net (value saved minus running cost). The morning brief surfaces the result each day.

**Validation**

`tests/test_build11.py` and `tests/test_gaps_client.py` cover event logging, the current-week hours calculation (including exclusion of older events), and the $48/hour valuation feeding real dollar figures into the brief.

**Pending**

The hours-saved side is complete. The net-return figure uses a placeholder operating cost of zero until you receive our monthly cost estimate (see Dependencies); once that is set, the weekly net is real from the first report.

**Evidence**

- `banks/activity_log.py`
- `banks/schedule.py`
- `banks/store/schema.sql`

---

## Task 2 — Daily Command Centre: Brief, Approvals, Scorecard, and Recap

**Status:** In progress — logic built and tested, approvals proven live in a test workspace; pending your Slack workspace and real data

**What We Built**

The daily surface you interact with in `#banks`: a failure-mode-first morning brief, one-click approvals, a weekly scorecard, and a nightly recap. The morning brief leads with anything approved but not yet sent (with its age), then today's top one to three items, vacancies, money due, the week's ROI, and market-brief freshness. Each draft carries Approve, Mark sent, Reject, and Revise buttons; approving triggers the send, and the separate mark-sent step gives you visibility on anything approved but not completed.

**Components**

`banks/briefing.py` assembles the ordered brief; `banks/scorecard.py` renders the weekly targets; `banks/reflection.py` produces the nightly recap; and `banks/jobs.py` with `banks/scheduler.py` fire each of these on cadence. Approvals run through `banks/approval.py` (button rendering and the two-step state machine) and `banks/socket_listener.py` (receiving clicks over an outbound connection, no public server), with `banks/reactions.py` as an emoji fallback.

**Validation**

`tests/test_briefing.py` fixes the brief's section order; `tests/test_approval.py` covers the two-step approve-then-mark-sent state; `tests/test_build11.py` covers the scorecard and nightly-recap posts. The approval buttons were also exercised live in a test Slack workspace.

**Pending**

The surface and its buttons work end-to-end. What remains is pointing it at **your** Banks Slack workspace (token, channel, invite — see Dependencies), after which the brief and scorecard populate from your real rentals, bills, and calendar as those sources connect. Until then the daily surface runs against stand-in data.

**Evidence**

- `banks/briefing.py`
- `banks/scorecard.py`
- `banks/reflection.py`
- `banks/jobs.py`
- `banks/approval.py`
- `banks/socket_listener.py`

---

## Task 3 — The Email-Reading Layer

**Status:** In progress — interface and offline testing complete; pending a live key and live verification

**What We Built**

The layer that reads the free-text of an email you forward and extracts the structured details Banks needs — the amount and due date of a bill, the vendor on a receipt, the requirements in a job posting. It is built behind a single interface so the rest of Banks never depends on which model is used, and so the whole pipeline can be tested without a live key.

**Components**

`banks/llmport.py` defines the interface with two implementations: a scripted stand-in used throughout the test suite, and a live Claude (Anthropic) adapter that activates only when its key is supplied. The reading layer is used by the bill, receipt, opportunity, and message-classification tasks below.

**Validation**

`tests/test_build11.py` covers the scripted stand-in and confirms the live adapter refuses to run without a key, so no test ever makes a network call.

**Pending**

The interface and the offline (scripted) path are complete and are what every other task tests against. The live Claude adapter is written but not yet run against the real service — that awaits your decision on the key (see Dependencies), after which we verify extraction quality on real forwarded emails.

**Evidence**

- `banks/llmport.py`

---

## Task 4 — Bill Pipeline and Expense Categorisation

**Status:** In progress — logic built and tested; pending the live mailbox and live reading layer

**What We Built**

A path that turns a forwarded bill into a tracked item: it reads the email, prepares the bill for your confirmation, and then reminds you seven days and one day before it is due — tracking and reminding only, never paying. Every bill is tagged personal or property-level from the outset, and property expenses roll up per property for your records.

**Components**

`banks/finance.py` holds the extraction (`extract_bill_from_email`), the confirmation draft, the seven-and-one-day reminder pass, the personal-versus-property tagging, and the per-property rollup. A `bill_category` column was added to the store schema to carry the tag.

**Validation**

`tests/test_gaps_client.py` covers the category defaulting and the per-property rollup; `tests/test_build11.py` covers extraction, the confirmation draft, and persisting a bill.

**Pending**

The full pipeline — extract, confirm, categorise, roll up, remind — is built and tested against stand-in emails. It goes live once (a) the mailbox exists so forwarded bills actually arrive, and (b) the reading layer is connected to its live key so extraction runs on real emails.

**Evidence**

- `banks/finance.py`
- `banks/store/schema.sql`

---

## Task 5 — Receipt Filing

**Status:** In progress — logic built and tested, one live Drive upload proven; pending the production authorisation, folder IDs, and live reading layer

**What We Built**

A path that files a forwarded receipt into the correct property folder in your Google Drive — or a personal folder when it is not tied to a property — while preserving the original attachment for tax and expense purposes.

**Components**

`banks/fileport.py` defines the storage interface with an in-memory stand-in and a live Google Drive adapter (using a one-time user authorisation, since a service account cannot write to a personal Drive). It parses the email, extracts the receipt details, resolves the destination folder per property with a personal fallback, and uploads the original attachment.

**Validation**

`tests/test_build11.py` and `tests/test_gaps_client.py` cover the folder-routing logic and an end-to-end filing against the in-memory stand-in. A live upload was also proven against Google Drive under the same one-time authorisation you will use.

**Pending**

The parsing, per-property routing, and upload are built and tested, and a real upload to Drive has been demonstrated. To run on your account it needs: the production Google authorisation (Option A in Dependencies), your per-property folder IDs, and the reading layer's live key for the extraction step.

**Evidence**

- `banks/fileport.py`

---

## Task 6 — Opportunity Pipeline

**Status:** In progress — logic built and tested; pending your resume (v14) and the live reading layer

**What We Built**

A path that takes a job posting you forward, matches it against your resume, flags any genuine gaps rather than writing around them, and prepares the application for your review — never submitting, and always showing you the posting first.

**Components**

`banks/opportunity.py` holds `process_forwarded_posting`, which extracts the posting, compares it to your career facts, records the opportunity, and produces the draft. A no-embellishment guard refuses to reference any fact not present in your resume.

**Validation**

`tests/test_build11.py` covers extraction, gap-flagging, and draft creation, and `tests/test_opportunity.py` covers the no-embellishment guard.

**Pending**

The matching, gap-flagging, and drafting logic is built and tested. It cannot produce a real draft until your resume (v14) reaches us — it is the sole source Banks draws from — and the reading layer is connected to its live key to interpret the posting.

**Evidence**

- `banks/opportunity.py`

---

## Task 7 — Message Classification and Surfacing

**Status:** In progress — logic built and tested; pending the live reading layer and live inbound messages

**What We Built**

A layer that sorts an incoming `#banks` message to the right handling — a tenant inquiry, a maintenance request, a bill, a posting, a review moment, an occasion — and, when it is not confident, asks you rather than guessing. Each recognised type is surfaced to you as a draft with approval buttons.

**Components**

`banks/classify.py` classifies the message and produces a confirmation draft when uncertain. The surfacing functions in `banks/rentals.py` turn a recognised event into a proposed draft: inquiries, maintenance dispatches, review requests, and occasion reminders.

**Validation**

`tests/test_build11.py` covers high-confidence classification, the confirm-on-ambiguity path, and the surfacing functions producing posted drafts.

**Pending**

The classification and surfacing logic is built and tested. Live operation needs the reading layer's key (for the classification step) and the real inbound channels — your Banks email address and Slack workspace — so real messages actually arrive to be sorted.

**Evidence**

- `banks/classify.py`
- `banks/rentals.py`

---

## Task 8 — Rental Operations Refinements

**Status:** In progress — logic built and tested; pending live PadSplit data

**What We Built**

Three refinements aligning the rental features to how you actually operate. Listing drafts now format per platform (PadSplit, Roomi, and others) through an extensible registry, so new platforms can be added without rebuilding. Review requests fire only at the moments you approved — a repair resolved promptly, a smooth move-in, or unprompted thanks — with the payment-streak trigger off by default. And applicant handling was corrected: Banks no longer screens independently; it relays PadSplit's own presented applicant for your decision.

**Components**

`banks/rentals.py` holds the per-platform listing formatter registry, the review-trigger gate, and the applicant-relay function. The previous independent income-and-credit scoring was removed, in line with your instruction that PadSplit screens and you decide.

**Validation**

`tests/test_gaps_client.py` covers the per-platform formats and registration, the approved-trigger gate with payment-streak off by default, and confirms the independent-screening code is gone; `tests/test_rentals.py` covers the applicant-relay path.

**Pending**

All three refinements are built and tested. They act on real rooms, vacancies, and applicants only once live PadSplit data is connected (read-only credentials — see Dependencies); until then they run against stand-in rooms.

**Evidence**

- `banks/rentals.py`

---

## Task 9 — Calendar, Market Brief, and Container

**Status:** In progress — logic built and tested, calendar proven live once; pending your calendar share and production wiring

**What We Built**

Read-only calendar conflict detection that treats your personal and family blocks as real commitments; a market-brief freshness clock that ingests the brief you paste daily and flags it stale if a day is missed rather than reasoning from old information; and a single wiring layer that connects every part of Banks to either its stand-in (for tests) or its live service (for production).

**Components**

`banks/calendarport.py` reads the calendar (with no write methods, so it is read-only by construction) and feeds `banks/schedule.py`'s conflict detection. `banks/briefport.py` stores the daily brief with a freshness timestamp and reports staleness. `banks/container.py` assembles the whole assistant, using stand-ins with no credentials for tests and failing loudly if a required live credential is missing.

**Validation**

`tests/test_calendarport.py` confirms read-only-by-construction and equal-weight conflicts; `tests/test_gaps_client.py` covers brief freshness and staleness; `tests/test_build11.py` covers the container. Calendar conflict detection was also run against a real event on a test calendar.

**Pending**

Conflict detection, the brief-freshness clock, and the wiring layer are built and tested, and calendar reading has been proven against a real calendar. Live operation needs you to share your calendar read-only (via the production Google authorisation in Dependencies); the market-brief clock is ready as soon as you begin pasting the daily brief into `#banks`.

**Evidence**

- `banks/calendarport.py`
- `banks/briefport.py`
- `banks/container.py`

---

## Task 10 — Capital Deployment Modelling

**Status:** Not started — deliberately held

**What We Built**

Nothing yet, by design. As you set out, no modelling proceeds until two gates are cleared: confirmation that the account sits with a custodian permitting alternative assets, and a legal review of prohibited-transaction exposure. We are holding to that.

**Pending**

Both gates. Once they clear, the work is scoped to short-term secured-lending underwriting only (loan-to-value, position in the capital stack, borrower experience, exit strategy, term) — never equity or syndication modelling, and Banks models and presents but never advises or acts.

---

## Dependencies — What Remains Before Going Live

Everything above is ready to switch on the moment the following are settled. The two highest-impact items are listed first; the rest can follow at your convenience.

### The two items that unblock the most

- **Your domain.** Once registered, sending it over (with access to manage its email settings) unblocks the mailbox, outbound sending, and your Banks email address — the single largest dependency.
- **Your Banks Slack workspace.** When the new free-tier workspace is set up, we'll need the bot token, the `#banks` channel ID, and an invite. We're on a stand-in workspace until then.

### The mailbox

- **The constraint.** You asked us to flag anything affecting the free Cloudflare mailbox before spending on a paid one. Here it is: **Cloudflare Email Routing is receive-only.** It forwards incoming mail to your address for free and does this very well, but it has no facility to *send* mail — it is not an outgoing mail server, and it cannot do "send-as." This is a design limitation of the free routing product, not a setting we can switch on.
- **Why it matters for Banks.** Sending an approved item on your behalf, and the send-as you asked for, both require the ability to send outbound — precisely the one thing Cloudflare alone cannot do.
- **The fix (still no paid mailbox).** We keep Cloudflare for receiving and add a dedicated **transactional email service** to handle the outgoing, approved drafts. A transactional sender is the right category of tool here: it sends one specific message on demand, as you, with proper delivery authentication (SPF/DKIM) so your mail lands in inboxes rather than spam. It is distinct from bulk or marketing email tools, which are the wrong fit for personal, one-at-a-time sending.
- **Our recommendation — Resend.** Resend is a modern transactional email service with a **free tier** that comfortably covers your volume, a quick setup, and clean support for sending from your own domain. We would configure it against your domain so everything goes out as you.
- **What we need from you.** Simply your go-ahead on this Cloudflare-plus-Resend approach — or, if you already use a transactional sender you'd prefer, tell us and we'll use that instead. We handle the entire setup once the domain is live.

### A few operating decisions

Each of these has a sensible default already built in — you can simply confirm the default, or redirect us:

1. **Tenant and vendor replies — routing.** Because Praise manages day-to-day tenant and prospect contact, our default is that Banks drafts to **Praise**, who sends onward, and Banks never messages a tenant or vendor directly. *Decision:* keep everything routed through Praise, or would you like Banks to draft straight to a tenant for certain routine cases?
2. **Turnover messages with housemates present.** In your co-living model a property stays occupied during a turnover, so drafts should account for the housemates not involved in that move. We don't want to invent an approach. *Decision:* how would you like those housemates handled — for example, a heads-up before cleaning or showings, quiet-hours notes, or any practice you already follow?
3. **How job postings reach Banks.** You listed the sources (LinkedIn, PropTech boards, industry networks); the open choice is the mechanism. *Decision:* (a) **you forward** a posting to Banks — available immediately, no logins; or (b) **Banks scans** those sites under your own login — more hands-off, but it needs your explicit go-ahead and access. Banks never submits either way, and always shows you the posting first.
4. **Interview-preparation briefs.** Not in your original notes, so it's currently off. *Decision:* would you like Banks to prepare a short interview-prep brief when an application advances?
5. **Calendar conflict sensitivity.** Banks already flags overlaps and treats personal and family blocks as real conflicts with equal weight. *Decision:* should it also flag back-to-back items with no travel time between them? (This works only if your events carry locations.)

### Items you mentioned would follow

1. **Your vendor list.** You noted Praise keeps the working vendor list by trade, to follow separately. Whenever it's handy, sending it over lets Banks address maintenance messages to the right vendor by name.
2. **Your current resume (v14).** You noted it was attached, but it didn't reach us — a resend would unblock the application drafts, which draw solely from that document (nothing inferred or invented).

### Two figures we owe you

- **Monthly operating cost.** The weekly ROI report compares the time Banks saves you against what Banks costs to run. So the report is accurate from the very first run, we'll provide a careful, deliberately conservative monthly running-cost estimate up front, then replace it with the real figure after a few weeks of actual usage — the numbers only get more accurate over time.
- **PadSplit access — the right form.** **Read-only login credentials** are the correct fit: Banks only ever reads from PadSplit and never changes anything there. This is the one and only integration that needs an actual login, because — unlike Google — PadSplit offers no "share" or "authorize" option. When you're ready, that's the form to provide, and we'll confirm the safest way to hand it over separately.

### The email-reading layer

- **What it is.** To act on an email you forward, Banks reads the text and pulls out the details (a bill's amount and due date, a receipt's vendor, a posting's requirements) using a small AI text model in the background. It's used only on the email content you forward, nothing else.
- **Our recommendation — a dedicated key.** A separate Claude (Anthropic) API key used only by Banks. It's a quick sign-up at Anthropic (we'll guide you through it) and keeps Banks fully independent, in line with keeping it entirely separate from Forced Action.
- **Alternative.** If you'd prefer, the same Claude key already used for Forced Action can be provided to Banks instead — it would just need to be supplied to us separately.
- **Cost.** Either way, usage at your email volume is negligible — on the order of a dollar or two a month.

### Setting up Google access — one decision first, then simple steps

Banks reads your calendar and files receipts into your Google Drive. Google grants this kind of access through a small "project" on its cloud platform, and there is one decision to make about who owns that project. We recommend the first option, as it keeps the work on our side and your effort to a single click later.

- **Option A (recommended) — we create and own the project.** We set up the Google project and its authorization entirely on our side, then add your Google account as an approved user. You never touch Google's technical console, create anything, or send us any keys or files. When the time comes, your only action is a single "Allow" click on a standard Google screen. Because the app is private to you, you may briefly see a "this app hasn't been verified by Google" notice; this is expected, and we'll show you exactly how to proceed.
- **Option B — you create the project.** You set up the Google project in your own account, enable the necessary services, generate the credentials, and send them to us. This gives you direct ownership of the project but is a genuinely technical process; we'd guide you, but it asks noticeably more of you than Option A.

*What we need now:* your choice of Option A or B, and the **Google account email address** you'll use for Banks (the same account that will hold your calendar and receipt folders). For Option A we use that address only to pre-approve your account so the later "Allow" step goes through smoothly — it's just the address, no password, nothing to set up.

**Once you've chosen, the remaining steps are simple and non-technical** (we'll send the exact link or screen for each; no password is ever shared with us, except the PadSplit read-only login above):

1. **Google Calendar** — share it with an address we provide, set to read-only. Banks reads your schedule to flag conflicts and can never change anything.
2. **Google Drive** — create one folder per property plus a "Personal" folder, all owned by you (or point us at a layout you already use).
3. **Google Drive** — the one-time "Allow" click described above.
4. **PadSplit** — provide the read-only login.
5. **Forward emails as they arrive** — bills, receipts, and postings, once your Banks address is live. Nothing to set up; just forward.

---

## Notes

- **Overall status: in active development, not yet live.** Every task's logic is built and covered by automated tests using stand-in data; each task's "Pending" line states exactly what remains before it runs against your real accounts.
- The common thread across the pending items is the same handful of dependencies — the domain, the Slack workspace, the reading-layer key, live PadSplit and Google access, and a few inputs from you — all listed in the Dependencies section. None require further build work; they are connections and inputs.
- Tasks are grouped by feature area; internal ticket codes and pull-request numbers are kept out of the report.
- All 128 automated tests pass. Live verifications performed against our own test accounts so far: a real email sent after approval, the Slack approval buttons, calendar conflict detection, and a Google Drive upload.
- The domain and mailbox unblock the most, and would help first.
