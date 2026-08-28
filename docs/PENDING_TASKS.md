# Banks — Pending Tasks (Maximum Distribution Build scope)

**Date:** 2026-08-27
**Scope:** items from `Banks_Maximum_Distribution_Build.md` that are not yet fully
delivered/proven. Internal refactors and code-review items are excluded — this
list is strictly against the signed spec.

Legend: 🔴 not done · 🟡 built, needs live activation/input · 🟢 done (listed for
completeness only where a sub-item lingers).

---

## MOD-01 — Application Intake, Dedup & Fit Scoring

- 🟡 **LoopCV intake** — spec names "LoopCV / Simplify." Simplify parser is live;
  the LoopCV export parser is dormant, awaiting one sample export from Josh to
  confirm columns.
- 🟡 **Forwarded email-confirmation listener** — built and unit-tested; not run
  live (needs a mailbox connection). Simplify/manual paths cover intake today.

## MOD-02 — Contact Resolution, Enrichment & Warm-Path Graph

- 🔴 **Verified contact enrichment via Hunter.io / Anymail Finder** — spec names
  these providers. Current path is Clay manual-CSV ($0 interim); automated
  verified-email enrichment needs a paid provider decision + wiring.

## MOD-03 — Distribution Lanes & Surround Workflow

- 🟡 **Real draft content** — lanes are built, but drafts pull only from
  `career-facts.md`, which is empty. Needs Josh's resume before any draft is real.
- 🟡 **Outbound email send of lanes** — `SmtpMailer` behind Relay is built and
  unit-tested; no live email has been sent. Needs a sending mailbox (Josh's
  Gmail + app password) to activate; otherwise Josh sends via "Mark sent."

## MOD-04 — Follow-up Cadence, Governance & Collision Ledger

- 🟢 Cadence (Day 3/7/14), reply-stop, collision freeze, governance caps, funnel
  tracking — all built and tested. No spec sub-item outstanding.

## MOD-05 — Slack Command & Control / Daily Attack Queue

- 🟡 **Threaded draft revisions** — the rewrite engine (LLM + guard + redraft) is
  built and proven; the in-thread reply-to-card trigger is being reworked to
  match Slack's threading model (Revise-button → next-message flow). Not yet
  delivered live.
- 🟡 **Interactive actions live in Josh's workspace** — Approve/Skip/Snooze/
  Mark-done are built; Approve/Mark-sent/Reject proven live in the test
  workspace. Full set goes live in Josh's workspace once the dedicated Banks
  Slack app is provisioned.
- 🟡 **On-demand retrieval live** — router (who-do-I-know / status / call-list)
  works with the real LLM; live in Josh's workspace pending the prod Slack app.

## MOD-06 — Adversarial Exclusion & Launch Staging

- 🟢 **Adversarial exclusion test suite** — done (6-case evasion matrix, both
  gates; caught + fixed a real whitespace-evasion bug).
- 🟢 **End-to-end mock run** — done (all-Fakes full pipeline with a planted
  exclusion).
- 🔴 **Live end-to-end run** — the real-services acceptance run (7-item checklist,
  `docs/launch/LAUNCH_ACCEPTANCE.md`). Not yet performed; by agreement it runs last.
- 🟢 **Clean codebase migration** — done (repo at github.com/jbkcreator/Banks).

## Delivery & Launch (spec §4)

- 🔴 **Production deployment** — the four feature branches (MOD-01→06) are not yet
  merged to `main`, and Banks is not deployed to a production host. Merge stack +
  provision prod environment + run the live E2E.

---

## Client inputs that gate the above (from `CLIENT_QUERIES_V2.md`)

| Input | Unblocks |
|---|---|
| Resume → `career-facts.md` | MOD-03 real draft content |
| Full exclusion list | MOD-06 live-E2E sign-off |
| Dedicated Banks Slack app (Socket Mode + Events) | MOD-05 live in Josh's workspace |
| Josh sending email + app password | MOD-03 automatic outreach send |
| Paid Clay / Hunter.io decision | MOD-02 automated verified enrichment |
| LoopCV sample export | MOD-01 LoopCV parser |

---

## Summary

Spec **coverage** is near-complete — code exists for essentially every named
deliverable. The pending work is: **two provider integrations** (Hunter/Anymail
enrichment, and activating email send), **the LoopCV parser**, **finishing the
threaded-revision trigger**, **the live end-to-end acceptance run**, and
**production deployment** — most of which are gated on the client inputs above,
not further engineering.
