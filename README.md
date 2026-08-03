# Banks

Josh's personal operations AI employee. Rentals, inbox, schedule, deadlines, research.

**Separate infrastructure. Separate invoice. Hard wall from all Forced Action systems.**
Drafts only, permanently — Banks never sends, posts, submits, pays, or transacts. Every
output is a draft awaiting Josh's tap in the private `#banks` Slack channel.

See `banks/constitution.md` for the full standing instructions (v2.1).

## Status

Plan → build in progress. Currently building the client-independent skeleton (constitution,
drafts-only enforcement, hard-wall harness, data engine, renderers, scheduler) against seeded
data. Live data sources (rentals, bills, calendar, Slack/mailbox credentials) plug in once
provisioned by Josh — see `.wayfinder/banks-build/BANKS-QUESTIONS-FOR-JOSH.md` in the parent
`FA` planning repo.

## Layout

```
banks/
  constitution.md       — v2.1 standing instructions (hashed; only Josh edits)
  config.py              — runtime config, loaded from env / personal secrets store
  enforcement.py         — drafts-only egress guard + operator verification
  slack.py                — #banks delivery (outbox dry-run until token provisioned)
  store/                  — SQLite schema + data access
  packets.py              — Decision Packet + Action Queue
  scorecard.py            — weekly scorecard + morning dashboard renderers
  scheduler.py            — standing-job cadence
  selfheal.py              — retry/dead-letter, degradation, temporal memory
  memory/                  — index.md, people.md, lessons.md, promises.md, career-facts.md
tests/                     — hard-wall acceptance harness + unit tests
outbox/                    — local draft landing zone (pre-Slack-token dry-run)
```

## Setup

```
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -e ".[dev]"
pytest
```
