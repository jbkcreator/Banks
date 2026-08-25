# Banks — What We Still Need From You (v2)

_For: the Maximum Distribution Build (MOD-01 to MOD-06)_
_Updated: 2026-08-25 · Deadline: Fri Aug 28, 2026_

You've already answered the big questions and sent the files (Simplify export,
LinkedIn connections, alumni list, recruiter registry, tier rules, scoring
weights, exclusion list, outreach examples). **Thank you — that's the hard part
done.** The build is running on all of it.

What's left is a short list of **access + two inputs**. Everything below is a
connection or a file — no more decisions to make. Once these land, we flip Banks
from test mode to your real accounts.

---

## 🔑 The 3 that unblock the most

**1. Anthropic (Claude) API key**
Banks uses a small AI model to read job descriptions and write your outreach
drafts. Without a key it runs on canned placeholder text.
→ Either a new key (quick sign-up at anthropic.com — we'll guide you), or reuse
the Claude key already used for Forced Action. Cost is ~$1–2/month at your volume.

**2. Slack access**
The `#banks-jobs` channel is confirmed (ID `C0BNGMYHFEF`). We just need the Banks
app installed in that workspace so it can actually post there.
→ Install the Banks Slack app in the Forced Action Leads workspace, invite it to
`#banks-jobs`, and send us the bot token. (We'll send exact click-by-click steps.)

**3. Your resume (v14)**
Banks writes applications and outreach using **only** what's in your resume — it
never invents or embellishes. It can't produce a real draft until it has the file.
→ Send the current resume. That's it.

---

## 📧 Email / mailbox (MOD-01 receiving + MOD-03 sending)

The build plan commits to email on both sides:
- **MOD-01** — _"a forwarded email confirmation listener to ingest external
  applications automatically without manual re-entry"_ (Banks **reads** an inbox).
- **MOD-03** — _"Resend dispatch helpers"_ and the lanes send _"personalized
  emails, connection notes, and follow-up sequences"_ (Banks **sends** as you).

We don't want to assume your setup — please tell us what you have / want:

**4a. Domain**
→ Do you have a domain for this, or will you register one? Which one?

**4b. Receiving — the mailbox Banks reads**
The forwarded-confirmation listener needs an inbox it can actually read.
→ What mailbox should Banks watch (a new `jobs@<domain>`, or an existing inbox)?
→ How would you prefer Banks to access it — Gmail/Workspace, a standard IMAP
mailbox, or a forwarding-to-webhook setup? Tell us what fits your stack; we'll
build the adapter to match. (Note: a plain forwarding service that only relays
mail elsewhere isn't enough on its own — Banks needs an inbox it can read.)

**4c. Sending — the address outreach goes out as**
→ What email address should outreach come from (`BANKS_FROM_EMAIL`)?
→ Do you have a transactional email sender you want us to use, or should we set
one up? Either way we handle the DNS/authentication (SPF/DKIM) — just tell us
your preference.

_What we need from you here: the domain, the mailbox + how we read it, and the
send address + sender preference. The adapters, DNS, and parser tuning are our
build._

---

## 🔎 One thing to decide (enrichment)

**5. Finding cold contact emails**
You picked Clay — but Clay's **free tier blocks all automated access** (we tested
it; the API is paywalled at ~$134/mo now). Good news: you mostly won't need it —
your LinkedIn export already includes emails, and your warm alumni/recruiters are
people you already know. For the occasional cold hiring manager, pick one:

- **(a)** Manual: Banks lists the few it can't find; you run them through Clay's
  free UI by hand. **$0.** _(our recommendation)_
- **(b)** Switch to Hunter.io / Anymail Finder (the tools in the original plan —
  proper APIs, free/cheap tiers).
- **(c)** Upgrade Clay to a paid plan.

→ Just tell us (a), (b), or (c).

---

## 🕒 Optional / later (not blocking)

- **LoopCV export** — you may set this up; when you do, send one export and we
  switch it on. Banks runs on Simplify only until then.
- **Production server** — deferred by agreement. Only needed for 24/7 running,
  not for building or testing.

---

## Summary

| # | Need | Type | Blocks |
|---|---|---|---|
| 1 | Anthropic API key | access | Real AI (reading + drafting) |
| 2 | Slack app install + bot token | access | Posting to `#banks-jobs` |
| 3 | Resume v14 | file | Application/outreach drafts |
| 4a | Domain | input | Email (both directions) |
| 4b | Mailbox Banks reads + access method | access | Forwarded-confirmation listener (MOD-01) |
| 4c | Send address + sender preference | input | Outreach email lanes (MOD-03) |
| 5 | Enrichment choice (a/b/c) | decision | Cold-contact emails |

Nothing here needs engineering — it's all connections and inputs. Send them over
as they're ready; #1–3 help first.
