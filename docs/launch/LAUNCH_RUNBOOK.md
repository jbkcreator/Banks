# Banks — Launch Runbook (MOD-06)

Ordered go-live sequence. The ordering *is* the deliverable: nothing acts on
Josh's real network before the exclusion list is loaded and the live acceptance
is signed. Do the steps in order.

## Go-live sequence

1. **Merge the branch stack bottom-up** to `main`, green CI at each step:
   `feature/mod01-mod02-foundation` → `feature/mod03-mod04-distribution` →
   `feature/mod05-command-control` → `feature/mod06-exclusion-launch`.
   (Merge order matters — each branch builds on the one below.)

2. **Tests green on `main`:** full suite, including the adversarial exclusion
   suite (`test_exclusion_adversarial.py`), the mock E2E (`test_e2e_mock.py`),
   the halt-freeze test (`test_halt_freeze.py`), and the hard-wall test.

3. **Secrets-in-history scan** clean — scan every commit (not just HEAD) for
   tokens/keys; confirm `.env`, `banks_live.db`, `.secrets/` were never
   committed. (Cheap insurance; the repo migration is already done.)

4. **Provision Josh's production Slack app** from `docs/banks_slack_app_manifest.yaml`;
   set the production `.env`: bot token (`xoxb-`), app token (`xapp-`), channel
   id, `BANKS_APPROVER_USER_ID` (single-approver lock), timezone, Anthropic key.
   Invite the bot to the channel.

5. **Load the two hard content dependencies:**
   - Josh's **full exclusion list** into `exclusions.txt`
     (`load_exclusions_from_file`) — former employers, competitors, named
     individuals, conflicts (CLIENT_QUERIES #11/#12).
   - Josh's **resume** into `banks/memory/career-facts.md` (no drafts have real
     content until this is filled — Banks refuses to invent).

6. **Live E2E acceptance** — run `docs/LAUNCH_ACCEPTANCE.md`; Josh signs all 7
   items. Do NOT proceed until signed.

7. **Turn on the scheduler** (the 7:30 daily-attack-queue job). Banks is live.

## Rollback / halt

- **Emergency stop:** send `stop all` or `stop banks` in the channel (or call
  `banks.halt.set_halt()`). This is a **real global freeze** — the scheduler
  skips its runs (jobs call `check_halt()` at entry) and Relay refuses to send
  (`relay_run` calls `check_halt()` first and raises `BanksHalted`). Approved
  intents stay `approved` and go once halt clears — nothing is lost.
- **Resume:** halt is in-process (not persisted) — **restart the process** to
  clear it. A restart is a deliberate resumption, never an accidental bypass.
- **Disable the scheduler** entirely if something is wrong: stop the scheduler
  process; the pipeline stops surfacing/sending on cadence. Manual/CLI paths
  remain available for controlled testing.
- **Un-send is impossible** — that's why every send is gated behind Josh's tap
  and the two exclusion gates. If a wrong draft was approved, the fastest guard
  is halt (before Relay runs) or `suppress_intent` on the draft_ref.
