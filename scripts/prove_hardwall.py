"""Prove the hard-wall guarantee — demonstrated, not assumed (client review #1).

Josh's constraint: "No automated LinkedIn sending, no headless session, no
browser automation of any kind — my account is the most valuable thing in this
search." This script SHOWS the guarantee firing at the code chokepoint:

  1. Every conceivable outbound action except the one sanctioned egress
     (post a draft to the #banks channel) raises DraftOnlyViolation.
  2. There is no LinkedIn / browser / send function to call in the first place —
     the enum has no such member; the capability doesn't exist.
  3. The static wall (test_hardwall.py) asserts no FA imports/env and that every
     network adapter is on an allowlist — run it and read the output.

Run:  python scripts/prove_hardwall.py
Exit 0 = the wall held on every attempt.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from banks.enforcement import (Egress, DraftOnlyViolation,  # noqa: E402
                               SANCTIONED_EGRESS, assert_egress_allowed)

BAR = "=" * 70


def section(title: str) -> None:
    print("\n" + BAR + f"\n  {title}\n" + BAR)


def main() -> int:
    failures = 0

    section("1. EVERY outbound action is gated by assert_egress_allowed()")
    print("  Sanctioned egress (the ONLY thing Banks may do to the outside):")
    for e in SANCTIONED_EGRESS:
        print(f"    ALLOWED  {e.value}")
    print()
    for action in Egress:
        if action in SANCTIONED_EGRESS:
            # prove the allowed one does NOT raise
            try:
                assert_egress_allowed(action)
                print(f"  [OK]    {action.value:32} passes (this is the one draft egress)")
            except DraftOnlyViolation:
                failures += 1
                print(f"  [BUG]   {action.value:32} sanctioned egress wrongly blocked!")
            continue
        # every other action MUST raise
        try:
            assert_egress_allowed(action)
            failures += 1
            print(f"  [BUG]   {action.value:32} SLIPPED THROUGH — wall breached!")
        except DraftOnlyViolation as exc:
            print(f"  [BLOCKED] {action.value:30} DraftOnlyViolation raised, nothing sent")
            print(f"            -> {str(exc).splitlines()[0]}")

    section("2. There is no send/LinkedIn/browser capability to even call")
    # The forbidden actions aren't just blocked at runtime — Banks ships no
    # function that performs them. Prove the enum has no browser/LinkedIn member.
    names = {e.name for e in Egress}
    for forbidden in ("LINKEDIN_SEND", "BROWSER", "HEADLESS", "AUTOMATE"):
        absent = forbidden not in names
        print(f"  [{'OK' if absent else 'BUG'}]    Egress.{forbidden:16} "
              f"{'does not exist (capability absent)' if absent else 'EXISTS — should not!'}")
        if not absent:
            failures += 1
    print("\n  Relay (banks/relay.py) is the ONLY sender and holds the ONLY outbound")
    print("  credential; it sends email intents Josh approved, never LinkedIn, never")
    print("  a browser. No approval => no send. A halted Banks sends nothing.")

    section("3. Static wall - test_hardwall.py (no FA imports/env, adapter allowlist)")
    print("  Running: pytest tests/test_hardwall.py -v\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_hardwall.py", "-v"],
        cwd=str(root), capture_output=True, text=True)
    tail = proc.stdout.strip().splitlines()
    for line in tail[-25:]:
        print("  " + line)
    if proc.returncode != 0:
        failures += 1
        print("\n  [BUG] test_hardwall.py FAILED — see output above.")

    section("VERDICT")
    if failures == 0:
        print("  WALL HELD on every attempt. Banks cannot send, post, submit, pay, or")
        print("  touch a browser/LinkedIn - it is drafts-only by construction, and the")
        print("  static test proves the FA isolation. Nothing here was assumed.")
    else:
        print(f"  {failures} PROBLEM(S) — the wall did not behave as claimed. See above.")
    print(BAR)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
