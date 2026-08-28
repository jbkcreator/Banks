# Banks — Live E2E Launch Acceptance Checklist

The live end-to-end run is the **last** step before go-live (MOD-06 / runbook
step 6). It runs against Josh's real workspace and accounts. Banks is **not
live** until Josh has signed off on all seven items below — each is a concrete
observable (what he sees in Slack) plus the record that backs it.

Run against: real Slack workspace + `BANKS_CHANNEL_ID`, real (or manual-CSV)
enrichment, real Anthropic key, Josh's loaded exclusion list + populated
`career-facts.md`.

| # | Item | Observable in Slack | Backing record |
|---|------|---------------------|----------------|
| 1 | Real job in → tiered card out | A Tier A/B card posts to the channel | `opportunities` row with tier set |
| 2 | Real hiring manager surfaced | Card names a real requisition owner (not generic HR) | `contacts` row attached to the opportunity |
| 3 | Verified email **or** LinkedIn fallback | Card shows a verified email, or a LinkedIn DM card when unverified | `contacts.verified` + lane type (`hiring_manager` vs `linkedin`) |
| 4 | Real warm path from his network | A 1st/2nd-degree path to the target is shown | `warm_intros` / referral path from imported contacts |
| 5 | Full surround pack + follow-up scheduled | Approving a Tier A posts the applicable lanes; a Day 3/7/14 follow-up is queued | `outreach_lanes` rows + `cadence_queue` rows |
| 6 | **Exclusion proof** | A planted excluded company/person never appears; if force-queued, it is blocked at send | intake `excluded` count > 0; `RelayResult.blocked` non-empty; `sent_receipts.status = 'suppressed'` |
| 7 | **Nothing sends without a tap** | No message leaves until Josh clicks Approve | `send_intents.status` stays `pending` until Approve; `opportunities.submitted` always 0 |

## Sign-off

- [ ] 1  Job → tiered card
- [ ] 2  Real hiring manager
- [ ] 3  Verified email / LinkedIn fallback
- [ ] 4  Warm path
- [ ] 5  Surround pack + follow-up
- [ ] 6  Exclusion blocks (draft + send)
- [ ] 7  Drafts-only holds

Signed (Josh): __________________________   Date: __________

All seven checked = launch gate cleared → proceed to runbook step 7 (scheduler on).
